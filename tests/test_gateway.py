import tempfile
import unittest
from unittest.mock import patch
from ops.kb_gateway import serve

class GatewayScopeTests(unittest.TestCase):
    def test_library_listing_and_retrieval_are_scoped(self):
        policy={'kb_id':'target','data':'/unused/data'}
        def forward(method,path,payload,token,policy):
            if method=='GET':
                return 200,{'status':'ok','data':{'kb_id':'target','kb_name':'Synthetic KB'}}
            self.assertEqual(payload['kb_names'],['Synthetic KB'])
            return 200,{'status':'ok','data':{}}
        with patch('ops.kb_gateway.forward',side_effect=forward):
            result=serve(policy,{'method':'GET','path':'/api/v1/knowledge-bases?page=1'})
            self.assertEqual(result['body']['data']['total'],1)
            serve(policy,{'method':'POST','path':'/api/v1/knowledge-bases/target/retrieve',
                          'body':{'kb_names':['foreign'],'query':'sample'}})

    def test_foreign_task_and_document_are_denied(self):
        with tempfile.TemporaryDirectory() as td:
            policy={'kb_id':'target','data':td+'/data'}
            with self.assertRaisesRegex(ValueError,'task_scope_denied'):
                serve(policy,{'method':'GET','path':'/api/v1/knowledge-bases/tasks/foreign'})
            with patch('ops.kb_gateway.forward',return_value=(200,{'data':{'doc_name':'user-file.md'}})), patch('ops.kb_gateway.backup') as backup:
                with self.assertRaisesRegex(ValueError,'delete_scope_denied'):
                    serve(policy,{'method':'DELETE','path':'/api/v1/knowledge-bases/target/documents/foreign'})
                backup.assert_not_called()
