import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,MagicMock,patch
from shoko.google_library import avatar_data,list_items
from shoko.storage import AppError

class GoogleLibraryTests(unittest.TestCase):
    def test_photo_rejects_arbitrary_hosts_credentials_and_active_mime(self):
        with patch('shoko.google_library.requests.get') as get:
            for url in ('http://lh3.googleusercontent.com/x','https://evil.test/photo','https://googleusercontent.com.evil.test/photo','https://user:secret@lh3.googleusercontent.com/x'):
                self.assertEqual(avatar_data(url),'')
            get.assert_not_called()
            response=get.return_value.__enter__.return_value
            response.status_code=200;response.headers={'Content-Type':'image/svg+xml'}
            self.assertEqual(avatar_data('https://lh3.googleusercontent.com/x'),'')

    def test_photo_is_bounded_does_not_follow_redirects_and_has_no_auth_header(self):
        with patch('shoko.google_library.requests.get') as get:
            response=get.return_value.__enter__.return_value
            response.status_code=200;response.headers={'Content-Type':'image/png'}
            response.iter_content.return_value=[b'png']
            self.assertEqual(avatar_data('https://lh3.googleusercontent.com/x'),'data:image/png;base64,cG5n')
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
            self.assertNotIn('headers',get.call_args.kwargs)
            response.iter_content.return_value=[b'x'*262145]
            self.assertEqual(avatar_data('https://lh3.googleusercontent.com/x'),'')

    def test_picker_is_read_only_escapes_query_and_supports_pagination(self):
        auth=MagicMock();session=auth.session.return_value.__enter__.return_value
        with patch('shoko.adapters.google_http_request') as request:
            request.return_value.status_code=200
            request.return_value.json.return_value={'files':[{'id':'sheet_fixture','name':'Копия'}],'nextPageToken':'next'}
            result=list_items(auth,{'google_auth_mode':'oauth'},'sheets',"A' or trashed = true",'page')
            self.assertEqual(result,{'items':[{'id':'sheet_fixture','name':'Копия'}],'next':'next'})
            self.assertEqual(request.call_args.args,(session,'GET','https://www.googleapis.com/drive/v3/files'))
            params=request.call_args.kwargs['params']
            self.assertIn("name contains 'A\\' or trashed = true'",params['q'])
            self.assertEqual(params['pageToken'],'page')
            self.assertEqual(params['pageSize'],50)

    def test_picker_rejects_unsupported_kind_and_service_account_before_network(self):
        auth=MagicMock()
        for config,kind in [({'google_auth_mode':'oauth'},'all'),({'google_auth_mode':'service_account'},'folders')]:
            with self.assertRaises(AppError):list_items(auth,config,kind)
        auth.session.assert_not_called()

    def test_picker_reports_google_error_without_document_body(self):
        auth=MagicMock()
        with patch('shoko.adapters.google_http_request') as request:
            request.return_value.status_code=403
            request.return_value.json.return_value={'error':{'code':403,'message':'private document data'}}
            with self.assertRaises(AppError) as error:list_items(auth,{'google_auth_mode':'oauth'},'folders')
            self.assertNotIn('private document data',str(error.exception))

if __name__=='__main__':unittest.main()
