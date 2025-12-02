import os, sys, json, datetime, requests

def upload_dropbox(token, local_path, remote_folder):
    url = "https://content.dropboxapi.com/2/files/upload"
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": json.dumps({"path": f"{remote_folder}/{os.path.basename(local_path)}",
                                       "mode": "overwrite", "mute": False}),
        "Content-Type": "application/octet-stream"
    }
    with open(local_path, "rb") as f:
        r = requests.post(url, headers=headers, data=f)
    r.raise_for_status()

def upload_yesterday(cfg):
    if not cfg["upload"]["enabled"]:
        return "disabled"
    token = cfg["upload"].get("dropbox_token","")
    if not token: return "no token"
    folder = cfg["upload"]["remote_folder"]
    data_dir = cfg["logging"]["data_dir"]
    prefix = cfg["logging"]["csv_prefix"]
    y = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    path = os.path.join(data_dir, f"{y}_{prefix}.csv")
    if not os.path.exists(path): return f"missing {path}"
    upload_dropbox(token, path, folder)
    return f"uploaded {path}"
