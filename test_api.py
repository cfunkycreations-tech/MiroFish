import requests, json

form = {'simulation_requirement': 'AI bubble test', 'project_name': 'test'}
files = [('files', ('test.txt', b'AI investment bubble test document', 'text/plain'))]
r = requests.post('http://localhost:5001/api/graph/ontology/generate', data=form, files=files, timeout=30)
d = r.json()
if r.status_code == 200:
    print('SUCCESS')
else:
    print('STATUS:', r.status_code)
    print('ERROR:', d.get('error', '')[:500])
    print('TRACEBACK:', d.get('traceback', '')[-2000:])
