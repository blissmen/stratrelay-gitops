import base64
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import library_event_bootstrap as b


class BootstrapTests(unittest.TestCase):
    def execute(self,apply=False,role_exists=False,fail_secret=False,probe=True):
        self.calls=[];self.created={}
        with tempfile.TemporaryDirectory() as root:
            migrations=Path(root)/'postgres/migrations';migrations.mkdir(parents=True)
            checks=[]
            for v in ('070','078','083'):
                path=migrations/(v+'_test.sql');path.write_text('test')
                checks.append([v,path.name,hashlib.sha256(b'test').hexdigest()])
            def run(args,input=None,**kwargs):
                self.calls.append((args,input))
                def ok(data):return SimpleNamespace(returncode=0,stdout=json.dumps(data),stderr='')
                if 'get' in args:
                    kind,name=args[args.index('get')+1:][:2]
                    if kind=='namespace':return ok({'metadata':{'uid':b.NAMESPACE_UID}})
                    if name=='trading-nats-auth':return ok({'data':{k:base64.b64encode(v.encode()).decode() for k,v in [('NATS_USER','test-user'),('NATS_PASSWORD','hidden-nats-password')]}})
                    return SimpleNamespace(returncode=1,stdout='',stderr='NotFound')
                if args[-1]==b.ADMIN_COMMAND:
                    if 'json_build_object' in input:return ok({'database':b.DATABASE,'primary':True,'migrations':checks})
                    if 'SELECT EXISTS' in input:return ok(role_exists)
                    self.assertIn('CREATE ROLE library_event_',input)
                    # Role creation must never precede its durable credential.
                    role=input.split('CREATE ROLE ')[1].split()[0]
                    self.assertIn(role.replace('_','-'),self.created)
                    return ok(None)
                if args[-1]==b.LOGIN_PROBE:return ok({'verified':probe})
                if 'create' in args:
                    if fail_secret:return SimpleNamespace(returncode=1,stdout='',stderr='hidden-secret-password')
                    obj=json.loads(input);self.created[obj['metadata']['name']]=obj
                    return ok(None)
                raise AssertionError('unexpected command')
            with patch('subprocess.run',side_effect=run):
                result=b.bootstrap('/test','local',root,apply)
        serialized=json.dumps(result)
        self.assertNotIn('hidden-',serialized)
        self.assertNotIn('postgresql://',serialized)
        return result

    def test_plan_does_not_create_roles_or_secrets(self):
        self.assertEqual(self.execute()['status'],'PLAN_ONLY')
        self.assertFalse(self.created)

    def test_secret_precedes_role_and_logins_audited(self):
        result=self.execute(apply=True)
        self.assertEqual(result['status'],'CREDENTIALS_VERIFIED')
        self.assertEqual(len(self.created),4)
        self.assertFalse(result['workloads_changed'])
        self.assertTrue(all(x['login_audit']['verified'] for x in result['components'].values()))

    def test_existing_unpaired_role_is_not_rotated(self):
        result=self.execute(apply=True,role_exists=True)
        self.assertEqual(result['reason'],'UNPAIRED_ROLE_REQUIRES_REVIEW')
        self.assertFalse(self.created)

    def test_failed_secret_prevents_role_creation(self):
        self.assertEqual(self.execute(apply=True,fail_secret=True)['status'],'BLOCKED')
        self.assertFalse(any('CREATE ROLE' in (payload or '') for _,payload in self.calls))

    def test_failed_login_is_not_ready_and_secret_is_retained(self):
        result=self.execute(apply=True,probe=False)
        self.assertEqual(result['reason'],'LOGIN_OR_PRIVILEGE_GATE_FAILED')
        self.assertEqual(len(self.created),1)

    def test_publication_grants_have_no_managed_or_broker_writes(self):
        sql=b.role_sql('orchestrator','a'*64)
        self.assertNotIn('GRANT UPDATE',sql)
        self.assertNotIn('GRANT ALL',sql)
        self.assertNotIn(' ON execution.',sql)
        self.assertNotIn(' ON execution_v2.',sql)
        self.assertNotIn(' ON trade_management.',sql)
        self.assertIn('research.lock_library_signal(text)',sql)
        for component in b.COMPONENTS:
            self.assertIn('NOINHERIT NOSUPERUSER',b.role_sql(component,'a'*64))
        with self.assertRaises(ValueError):b.role_sql('orchestrator',"unsafe'password")


if __name__=='__main__':unittest.main()
