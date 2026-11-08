"""End-to-end check of Phase 2 against a RUNNING server (and real S3 unless --skip-s3).

    python scripts/smoke_phase2.py                      # http://localhost:8000
    python scripts/smoke_phase2.py --skip-s3            # skip the real S3 upload/download
    python scripts/smoke_phase2.py --file C:\\docs\\cv.pdf --keep   # upload YOUR file, keep it afterwards
Uses only the standard library. Creates a throw-away user and deletes the test resource.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from http.cookiejar import CookieJar

parser = argparse.ArgumentParser()
parser.add_argument("--base", default="http://localhost:8000")
parser.add_argument("--skip-s3", action="store_true", help="do not PUT/GET against S3")
parser.add_argument("--file", help="path of a real .pdf / .docx / .txt file to upload instead of the sample text")
parser.add_argument("--keep", action="store_true", help="do not delete the test resource (inspect it in S3 and /admin)")
args = parser.parse_args()

MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}

jar = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
failures = []


def call(method, path, body=None):
    headers = {"Content-Type": "application/json"}
    token = next((c.value for c in jar if c.name == "csrftoken"), None)
    if token:
        headers["X-CSRFToken"] = token
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(args.base + path, data=data, method=method, headers=headers)
    try:
        with opener.open(request) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw) if raw else None


def check(name, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + name + ("" if condition else f"   -> {detail}"))
    if not condition:
        failures.append(name)


status, body = call("GET", "/api/v1/health/")
check("health", status == 200, body)

email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
status, body = call("POST", "/api/v1/auth/register", {"email": email, "name": "Smoke", "password": "correct-horse-battery-9"})
check("register", status == 201, body)
call("GET", "/api/v1/auth/me")

status, created = call("POST", "/api/v1/resources", {
    "source_type": "INSTAGRAM_REEL", "source_url": "https://www.instagram.com/reel/smoke/",
    "creator": "smoke", "resource_url": "https://www.notion.so/smoke", "tags": ["Smoke", "Test"],
})
check("create resource", status == 201 and created["resource_type"] == "NOTION", created)
resource_id = created["id"]
path = f"/api/v1/resources/{resource_id}"

status, body = call("GET", "/api/v1/resources?limit=5&tag=smoke")
check("list + tag filter", status == 200 and body["count"] == 1, body)

status, body = call("GET", path)
check("detail", status == 200 and body["system"]["version"] == 1, body)

status, body = call("PATCH", path, {"version": 1, "title": "Smoke title"})
check("patch bumps version", status == 200 and body["system"]["version"] == 2, body)
status, body = call("PATCH", path, {"version": 1, "title": "stale"})
check("stale patch -> 409", status == 409 and body["error"]["code"] == "VERSION_CONFLICT", body)

if args.file:
    with open(args.file, "rb") as handle:
        content = handle.read()
    filename = os.path.basename(args.file)
    mime_type = MIME_BY_EXTENSION.get(os.path.splitext(filename)[1].lower(), "application/octet-stream")
else:
    content, filename, mime_type = b"hello from the FindThatShit smoke test", "smoke.txt", "text/plain"
status, upload = call("POST", path + "/upload-url", {"filename": filename, "mime_type": mime_type, "size": len(content)})
check("upload-url", status == 200 and upload.get("upload_url"), upload)
if status == 200:
    print(f"      S3 key: {upload['s3_object_key']}")
status, body = call("POST", path + "/upload-url", {"filename": "x.exe", "mime_type": "application/x-msdownload", "size": 5})
check("bad type rejected", status == 400 and body["error"]["code"] == "UNSUPPORTED_FILE_TYPE", body)

if args.skip_s3:
    print("SKIP  S3 upload/download (--skip-s3)")
else:
    status, body = call("GET", path + "/download-url")
    check("download before upload -> 409", status == 409 and body["error"]["code"] == "FILE_NOT_UPLOADED", body)
    put = urllib.request.Request(upload["upload_url"], data=content, method="PUT", headers=upload["headers"])
    try:
        with urllib.request.urlopen(put) as response:
            check("PUT file to S3", response.status == 200)
    except urllib.error.HTTPError as error:
        check("PUT file to S3", False, f"{error.code} {error.read()[:300]!r}")
    status, body = call("GET", path + "/download-url")
    check("download-url after upload", status == 200 and body.get("url"), body)
    if status == 200:
        with urllib.request.urlopen(body["url"]) as response:
            check("downloaded bytes match", response.read() == content)

if args.keep:
    print(f"KEEP  resource {resource_id} left in place (user {email})")
else:
    status, body = call("DELETE", path)
    check("delete", status == 204, body)
    status, body = call("GET", path)
    check("deleted resource is 404", status == 404, body)

print("\nALL PASSED" if not failures else f"\n{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)