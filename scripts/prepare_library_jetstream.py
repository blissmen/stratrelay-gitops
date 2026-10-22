"""Render the research-only cutover from the released platform checkout.

Requires PyYAML and platform-tools on PYTHONPATH. Writes GitOps files only.
Never connects to Kubernetes, PostgreSQL, NATS or a broker.
"""
import argparse
import copy
import json
from pathlib import Path
import yaml
from research.library_event_plan import plan
from research.strategy_library_registration import registration

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/trading-platform'
KEYS = ['donchian_breakout', 'ema_pullback', 'fvg_retracement']
OLD_RUN = 'library-canary-20261016-v1'
DATABASE = 'trading_platform'
ROLES = ['relay', 'observer', 'orchestrator']
SECURITY = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
            'capabilities': {'drop': ['ALL']}}


def literal(dumper, value):
    return dumper.represent_scalar('tag:yaml.org,2002:str',value,style='|' if '\n' in value else None)


yaml.SafeDumper.add_representer(str,literal)


def resource(kind, name, **fields):
    return {'apiVersion': {'Deployment': 'apps/v1', 'Job': 'batch/v1',
            'NetworkPolicy': 'networking.k8s.io/v1'}.get(kind, 'v1'),
            'kind': kind, 'metadata': {'name': name, 'namespace': 'trading'}, **fields}


def generate(run_id, activation_time):
    release = json.loads((APP/'release.json').read_text())
    if release['commit'] != '2c333a98fa59c711dc167ab2834966ab879a7cfb':
        raise ValueError('Review new release source before regenerating the frozen cutover')
    image = next(x['reference'] for x in release['images'] if x['name']=='trading-platform-runtime')
    bundle = plan(run_id, activation_time, image, KEYS)
    bundle['readiness']['database']=DATABASE
    name = 'library-events-' + run_id
    if len(name) > 63:
        raise ValueError('run ID too long for Kubernetes resources')
    config = resource('ConfigMap', name, immutable=True, data={
        'run.json': json.dumps(bundle['manifest'], indent=2)+'\n',
        'release.json': json.dumps({'image':image,'commit':release['commit']})+'\n',
        'activation.sql': bundle['activation_sql'],
        'quiesce.sql': bundle['quiesce_sql'],
        'activate.py': (ROOT/'docs/library-jetstream/activate.py').read_text(),
        'gate.py': (ROOT/'docs/library-jetstream/gate.py').read_text(),
        'health.py': (ROOT/'docs/library-jetstream/health.py').read_text(),
    })
    docs = [config]
    for role in ROLES + ['provision-streams', 'activate']:
        workload_name = 'library-event-'+role
        labels = {'app.kubernetes.io/name': workload_name,
                  'stratrelay.app/library-events': 'true', 'stratrelay.app/status': 'research'}
        secret = 'library-event-'+role
        env = [{'name': k, 'value': v} for k,v in {
            'LIBRARY_EVENT_TRANSPORT': 'JETSTREAM', 'LIBRARY_EVENT_DATABASE': DATABASE,
            'EXECUTION_AUTHORITY_MODE': 'DISABLED', 'PGCONNECT_TIMEOUT': '10',
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
            'PYTHONPATH': '/app',
            'SIGNAL_AUTHORITY_MODE': 'DB_PRIMARY', 'SIGNAL_DB_PRIMARY_ENABLED': 'true',
            'SIGNAL_JETSTREAM_PRIMARY_ENABLED': 'true'}.items()]
        command = (['python','/config/activate.py'] if role=='activate' else
                   ['python','-m','research.library_event_service',role] +
                   (['--manifest','/config/run.json'] if role in ['observer','orchestrator'] else []))
        container = {'name': role, 'image': image, 'command': command,
            'envFrom': [{'secretRef': {'name': secret}}], 'env': env,
            'securityContext': copy.deepcopy(SECURITY),
            'resources': {'requests': {'cpu':'50m','memory':'128Mi'},
                          'limits': {'cpu':'500m','memory':'768Mi'}},
            'volumeMounts': [{'name':'manifest','mountPath':'/config','readOnly':True},
                             {'name':'tmp','mountPath':'/tmp'}]}
        if role=='orchestrator':
            container['envFrom'].append({'secretRef': {'name':'trading-signal-cutoff'}})
        pod = {'automountServiceAccountToken': False,
               'imagePullSecrets': [{'name':'ghcr-pull'}],
               'securityContext': {'runAsNonRoot':True,'runAsUser':65532,'runAsGroup':65532,
                                   'seccompProfile': {'type':'RuntimeDefault'}},
               'containers':[container], 'volumes':[
                   {'name':'manifest','configMap':{'name':name}}, {'name':'tmp','emptyDir':{}}]}
        template = {'metadata': {'labels': labels}, 'spec': pod}
        if role in ROLES:
            gate = copy.deepcopy(container)
            gate.update(name='verify-activation', command=['python','/config/gate.py'])
            pod['initContainers']=[gate]
            container['readinessProbe']={'exec':{'command':['python','/config/health.py',role]},
                'periodSeconds':30,'timeoutSeconds':15,'failureThreshold':3}
            deployment=resource('Deployment',workload_name,spec={'replicas':1,
                'strategy':{'type':'Recreate'},'selector':{'matchLabels':{'app.kubernetes.io/name':workload_name}},
                'template':template})
            deployment['metadata']['labels']=labels
            docs.append(deployment)
        else:
            pod['restartPolicy']='Never'
            if role=='activate':
                pod['serviceAccountName']='library-event-cutover-reader'
                pod['automountServiceAccountToken']=True
            job=resource('Job',workload_name+'-'+run_id,spec={'backoffLimit':6,
                'activeDeadlineSeconds':900,'template':template})
            job['metadata']['labels']=labels
            docs.append(job)
    (APP/'library-jetstream.yaml').write_text(yaml.safe_dump_all(docs,sort_keys=False))
    names = ['library-event-'+r for r in ROLES+['provision-streams','activate']]
    network=[]
    for target,port in [('trading-postgres',5432),('trading-nats',4222)]:
        network.append(resource('NetworkPolicy','library-events-'+target+'-ingress',spec={
            'podSelector':{'matchLabels':{'app.kubernetes.io/name':target}},'policyTypes':['Ingress'],
            'ingress':[{'from':[{'podSelector':{'matchLabels':{'stratrelay.app/library-events':'true'}}}],
                        'ports':[{'port':port,'protocol':'TCP'}]}]}))
    network.append(resource('NetworkPolicy','library-events-egress',spec={
        'podSelector':{'matchLabels':{'stratrelay.app/library-events':'true'}},
        'policyTypes':['Ingress','Egress'],'ingress':[], 'egress':[
            {'to':[{'podSelector':{'matchLabels':{'app.kubernetes.io/name':target}}}],
             'ports':[{'port':port,'protocol':'TCP'}]}
            for target,port in [('trading-postgres',5432),('trading-nats',4222)]]+[
            {'to':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'kube-system'}},
                     'podSelector':{'matchLabels':{'k8s-app':'kube-dns'}}}],
             'ports':[{'port':53,'protocol':p} for p in ['UDP','TCP']]}]}))
    # Only the short-lived activation Job can read the Kubernetes API.
    network.append(resource('NetworkPolicy','library-event-activate-apiserver',spec={
        'podSelector':{'matchLabels':{'app.kubernetes.io/name':'library-event-activate'}},
        'policyTypes':['Egress'],'egress':[{'to':[{'ipBlock':{'cidr':'10.43.0.1/32'}}],
            'ports':[{'port':443,'protocol':'TCP'}]},
            {'to':[{'ipBlock':{'cidr':'10.10.10.10/32'}}],
             'ports':[{'port':6443,'protocol':'TCP'}]}]}))
    (APP/'library-jetstream-network.yaml').write_text(yaml.safe_dump_all(network,sort_keys=False))
    # Source remains one reader. A merge requires provisioned credentials; missing
    # credentials would interrupt this shared Recreate collector, so draft is mandatory.
    collector=(APP/'market-data-collector.yaml').read_text()
    if 'name: MARKET_DATA_EVENT_MODE' not in collector:
        collector=collector.replace('          env:\n','          env:\n'+
            '            - {name: MARKET_DATA_EVENT_MODE, value: JETSTREAM_OUTBOX}\n'+
            '            - {name: LIBRARY_EVENT_DATABASE, value: '+DATABASE+'}\n'+
            '            - name: LIBRARY_EVENT_DSN\n'+
            '              valueFrom:\n'+
            '                secretKeyRef: {name: library-event-collector, key: LIBRARY_EVENT_DSN}\n',1)
    collector=collector.replace('{name: LIBRARY_EVENT_DATABASE, value: trading}',
        '{name: LIBRARY_EVENT_DATABASE, value: '+DATABASE+'}')
    collector=collector.replace('Read-only: no broker writes, no DB writes.',
        'No broker writes. Event mode adds durable research evidence/outbox writes.')
    (APP/'market-data-collector.yaml').write_text(collector)
    old=(APP/'strategy-library-canary.yaml').read_text()
    old=old.replace('  replicas: 1','  replicas: 0',1)
    (APP/'strategy-library-canary.yaml').write_text(old)
    k=(APP/'kustomization.yaml').read_text()
    for f in ['library-jetstream.yaml','library-jetstream-network.yaml','library-jetstream-rbac.yaml']:
        if f not in k:k+='\n  - '+f+'\n'
    (APP/'kustomization.yaml').write_text(k)
    (ROOT/'docs/library-jetstream/readiness.json').write_text(json.dumps(bundle['readiness'],indent=2)+'\n')
    print(json.dumps({'status':'PREPARED_NOT_ACTIVATED','run_id':run_id,'image':image,
                      'activation_time':activation_time,'keys':KEYS,'broker_writes':0}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-id',required=True);p.add_argument('--activation-time',type=int,required=True)
    a=p.parse_args();generate(a.run_id,a.activation_time)
