import json
import subprocess

def xo_cli(action, args={}, check=True, simple_output=True, use_json=False):
    run_array = ['xo-cli', action]
    if use_json:
        run_array += ['--json']
    run_array += ["%s=%s" % (key, value) for key, value in args.items()]
    res = subprocess.run(
        run_array,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check
    )
    if simple_output:
        output = res.stdout.decode().strip()
        if use_json:
            return json.loads(output)
        return output
    return res

def xo_object_exists(uuid):
    lst = json.loads(xo_cli('--list-objects', {'uuid': uuid}))
    return len(lst) > 0
