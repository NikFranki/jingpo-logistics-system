"""Exercise the HTTP contract using a subprocess bound only to the test DB."""
import json
import os
import socket
import subprocess
import sys
import time
import unittest
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4
from sqlalchemy.engine import make_url

@unittest.skipUnless((make_url(os.environ['DATABASE_URL']).database or '').endswith('_test') if os.getenv('DATABASE_URL') else False, 'requires *_test database')
class V3ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port=sock.getsockname()[1]
        cls.base=f'http://127.0.0.1:{port}'
        cls.server=subprocess.Popen([sys.executable,'-m','uvicorn','main:app','--host','127.0.0.1','--port',str(port)], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.stop_server)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:
                with urlopen(cls.base+'/health/ready',timeout=1) as response:
                    if response.status==200:
                        return
            except (URLError,TimeoutError):
                if cls.server.poll() is not None:
                    raise RuntimeError('test backend stopped during startup')
                time.sleep(.1)
        raise RuntimeError('test backend did not become ready')

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.server.kill(); cls.server.wait()

    def request(self,path,method='GET',body=None,key=None,legacy=True):
        headers={'Content-Type':'application/json'}
        if method!='GET':
            headers['Idempotency-Key']=key or str(uuid4())
        request=Request(self.base+'/api/v1'+path,method=method,headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            response=urlopen(request,timeout=5)
        except HTTPError as error:
            response=error
        with response:
            payload=json.load(response)
            if response.status>=400:
                self.assertEqual(response.headers['X-Request-ID'],payload['request_id'])
            return response.status,payload

    def station(self,code,**flags):
        status,body=self.request('/stations','POST',{'code':code,'name':code,**flags})
        self.assertEqual(status,201,body)
        return body

    def test_network_errors_filters_and_immutable_fields(self):
        code='API_'+uuid4().hex[:8].upper()
        key=str(uuid4())
        request={'code':code,'name':' API 名称 ','allows_delivery':True}
        status,created=self.request('/stations','POST',request,key)
        self.assertEqual(status,201,created)
        self.assertEqual(created['name'],'API 名称')
        self.assertEqual(self.request('/stations','POST',request,key),(201,created))
        status,error=self.request('/stations','POST',request)
        self.assertEqual((status,error['error']['code']),(409,'NETWORK_CODE_CONFLICT'))
        status,error=self.request('/stations/'+created['id'],'PATCH',{'code':'CHANGE'})
        self.assertEqual((status,error['error']['code']),(422,'VALIDATION_ERROR'))
        self.assertEqual(self.request('/stations/999999999','PATCH',{'name':'missing'})[0],404)
        patch_key=str(uuid4())
        status,disabled=self.request('/stations/'+created['id'],'PATCH',{'enabled':False},patch_key)
        self.assertEqual(status,200,disabled)
        self.assertFalse(disabled['enabled'])
        self.assertEqual(self.request('/stations/'+created['id'],'PATCH',{'enabled':False},patch_key),(200,disabled))
        self.assertIn(created['id'],[s['id'] for s in self.request('/stations')[1]])
        self.assertNotIn(created['id'],[s['id'] for s in self.request('/stations?enabled=true')[1]])
        target=self.station(code+'_D',allows_delivery=True)
        status,error=self.request('/routes','POST',{'code':code+'_R','origin_station_id':int(created['id']),'destination_station_id':int(target['id'])})
        self.assertEqual((status,error['error']['code']),(409,'NETWORK_RESOURCE_DISABLED'))
        status,error=self.request('/routes','POST',{'code':code+'_L','origin_station_id':int(target['id']),'destination_station_id':int(target['id'])})
        self.assertEqual((status,error['error']['code']),(409,'INVALID_NETWORK_CONFIGURATION'))
        self.assertEqual(self.request('/transport-tasks/candidates?route_code=DOES_NOT_EXIST')[0],404)
