"""Optional SOCKS5 transport for OpenSSH; SSH still authenticates the pinned host."""
import ipaddress
import socket
import struct
import sys
import threading

def exact(sock, count):
    result=b""
    while len(result)<count:
        part=sock.recv(count-len(result))
        if not part:
            raise OSError("proxy_closed")
        result+=part
    return result

def connect(proxy_host, proxy_port, target, port):
    sock=socket.create_connection((proxy_host,proxy_port),timeout=10)
    try:
        sock.sendall(b"\x05\x01\x00")
        if exact(sock,2)!=b"\x05\x00":
            raise OSError("proxy_auth_unsupported")
        try:
            address=ipaddress.ip_address(target)
            dest=(b"\x01" if address.version==4 else b"\x04")+address.packed
        except ValueError:
            name=target.encode("idna")
            if len(name)>255:
                raise OSError("proxy_invalid_target")
            dest=b"\x03"+bytes([len(name)])+name
        sock.sendall(b"\x05\x01\x00"+dest+struct.pack("!H",port))
        reply=exact(sock,4)
        if reply[:2]!=b"\x05\x00":
            raise OSError("proxy_connect_failed")
        size={1:4,4:16}.get(reply[3])
        if reply[3]==3:
            size=exact(sock,1)[0]
        if size is None:
            raise OSError("proxy_invalid_response")
        exact(sock,size+2)
        sock.settimeout(None)
        return sock
    except BaseException:
        sock.close()
        raise

def main():
    try:
        with connect(sys.argv[1],int(sys.argv[2]),sys.argv[3],int(sys.argv[4])) as sock:
            def send():
                try:
                    while data:=sys.stdin.buffer.read1(65536):
                        sock.sendall(data)
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
            threading.Thread(target=send,daemon=True).start()
            while data:=sock.recv(65536):
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
        return 0
    except (OSError,ValueError,IndexError):
        print("SSH proxy transport failed",file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
