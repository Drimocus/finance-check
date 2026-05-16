import json, os

CCP_DATEFORMAT = "%Y-%m-%dT%H:%M:%SZ"
# existing database uses slightly different format
BRAVE_DATEFORMAT = "%Y-%m-%d %H:%M:%S"

def read_json_file(filename, mode='r', encoding="utf-8") -> dict:
    file = open(file=filename, mode=mode, encoding=encoding)
    res = json.load(file)
    file.close()
    return res

def write_json_file(
    contents, 
    filename, 
    create_dirs=True, 
    mode='w', 
    encoding="utf-8"
):
    if mode == 'wb' : encoding=None
    # ensure directory for file exists if not creating in current directory
    dirname = os.path.dirname(filename)
    if (create_dirs and dirname != ''):
        os.makedirs(os.path.dirname(filename), exist_ok=True)
    file = open(file=filename, mode=mode, encoding=encoding)
    json.dump(contents, file, indent=4)
    file.close()

config = read_json_file("config/tax_check_config.json")