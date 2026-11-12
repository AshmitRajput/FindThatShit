"""Live Phase 3 check (stdlib only). Needs: API running, Celery worker running, real S3 + AI keys in .env.

  python scripts/smoke_phase3.py --file C:\\path\\resume.pdf
  python scripts/smoke_phase3.py --skip-ai      # only checks the job reaches a final state
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.cookiejar import CookieJar

MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}
PASSWORD = "correct-horse-battery-9"
POST_TEXT = "Free resume template for software engineering job applications. ATS friendly layout."
failures = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(name)
    return ok


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def csrf(self):
        return next((c.value for c in self.jar if c.name == "csrftoken"), "")

    def call(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if method not in ("GET", "HEAD") and self.csrf():
            request.add_header("X-CSRFToken", self.csrf())
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with self.opener.open(request, timeout=60) as response:
                raw, status = response.read(), response.status
        except urllib.error.HTTPError as error:
            raw, status = error.read(), error.code
        try:
            return status, (json.loads(raw) if raw else None)
        except ValueError:
            return status, None


def register(base, label):
    client = Client(base)
    email = f"smoke-{label}-{uuid.uuid4().hex[:8]}@example.com"
    status, _ = client.call("POST", "/api/v1/auth/register", {"email": email, "name": label, "password": PASSWORD})
    if not client.csrf():
        client.call("GET", "/api/v1/auth/me")
    check(f"register user {label}", status == 201, str(status))
    return client


def error_code(body):
    return ((body or {}).get("error") or {}).get("code")


def wait_for_terminal(client, resource_id, timeout):
    deadline, last = time.time() + timeout, None
    while time.time() < deadline:
        status, body = client.call("GET", f"/api/v1/resources/{resource_id}/processing")
        if status != 200:
            return None
        state = (body["processing_status"], body["stage"], body["attempt"], body["error_code"])
        if state != last:
            print(f"        status={state[0]} stage={state[1]} attempt={state[2]} error={state[3]}")
            last = state
        if body["processing_status"] in ("PROCESSED", "FAILED"):
            return body
        time.sleep(2)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--file")
    parser.add_argument("--skip-ai", action="store_true")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    status, body = Client(args.base).call("GET", "/api/v1/health/")
    check("health ok", status == 200 and body and body.get("status") == "ok", str(body))

    alice = register(args.base, "alice")
    base_payload = {"source_type": "INSTAGRAM_REEL", "source_url": "https://www.instagram.com/reel/abc/",
                    "creator": "smoke", "post_text": POST_TEXT}

    # 1. parallel duplicate creates with one key -> exactly one resource
    race_key, results = uuid.uuid4().hex, []
    threads = [threading.Thread(target=lambda: results.append(
        alice.call("POST", "/api/v1/resources", base_payload, {"Idempotency-Key": race_key}))) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    ids = {b["id"] for s, b in results if s == 201 and b}
    check("5 parallel creates with one key give one resource", len(ids) == 1 and all(s == 201 for s, _ in results),
          str([s for s, _ in results]))
    _, listing = alice.call("GET", "/api/v1/resources")
    check("only one resource exists after the race", listing and listing["count"] == 1, str(listing and listing["count"]))

    # 2. create with key, replay, conflict
    key = uuid.uuid4().hex
    s1, r1 = alice.call("POST", "/api/v1/resources", {**base_payload, "title": ""}, {"Idempotency-Key": key})
    s2, r2 = alice.call("POST", "/api/v1/resources", {**base_payload, "title": ""}, {"Idempotency-Key": key})
    check("create 201 and replay returns same id", s1 == 201 and s2 == 201 and r1["id"] == r2["id"])
    s3, r3 = alice.call("POST", "/api/v1/resources", {**base_payload, "creator": "other"}, {"Idempotency-Key": key})
    check("same key, different payload -> 409 IDEMPOTENCY_CONFLICT", s3 == 409 and error_code(r3) == "IDEMPOTENCY_CONFLICT")
    resource_id = r1["id"]

    # 3. optional file upload straight to S3
    if args.file:
        ext = os.path.splitext(args.file)[1].lower()
        data = open(args.file, "rb").read()
        s, info = alice.call("POST", f"/api/v1/resources/{resource_id}/upload-url",
                             {"filename": os.path.basename(args.file), "mime_type": MIME.get(ext, ""), "size": len(data)})
        check("upload-url 200", s == 200, str(info))
        put = urllib.request.Request(info["upload_url"], data=data, method="PUT", headers=info["headers"])
        try:
            with urllib.request.urlopen(put, timeout=120) as response:
                check("PUT file to S3", response.status == 200, str(response.status))
        except urllib.error.HTTPError as error:
            check("PUT file to S3", False, f"{error.code} {error.read()[:200]!r}")

    # 4. process, replay, duplicate while active
    pkey = uuid.uuid4().hex
    sp, job1 = alice.call("POST", f"/api/v1/resources/{resource_id}/process", {}, {"Idempotency-Key": pkey})
    check("process -> 202", sp == 202 and job1 and job1["processing_status"] == "PROCESSING", str(job1))
    _, job2 = alice.call("POST", f"/api/v1/resources/{resource_id}/process", {}, {"Idempotency-Key": pkey})
    check("process replay returns same job", job2 and job1["job_id"] == job2["job_id"])
    _, job3 = alice.call("POST", f"/api/v1/resources/{resource_id}/process", {})
    check("process while active returns same job", job3 and job1["job_id"] == job3["job_id"])

    # 5. wait for the worker
    print("      waiting for the Celery worker...")
    final = wait_for_terminal(alice, resource_id, args.timeout)
    if args.skip_ai:
        check("job reached a final state", final is not None)
    else:
        check("resource PROCESSED", final is not None and final["processing_status"] == "PROCESSED", str(final))
        _, detail = alice.call("GET", f"/api/v1/resources/{resource_id}")
        if detail and final and final["processing_status"] == "PROCESSED":
            check("description, summary, language, tags saved",
                  bool(detail["resource"]["description"] and detail["ai_metadata"]["summary"]
                       and detail["ai_metadata"]["language"] and detail["ai_metadata"]["tags"]))
            check("version bumped by AI save", detail["system"]["version"] >= 2)
            check("last_processed_at set", detail["system"]["last_processed_at"] is not None)
            print(f"      title: {detail['resource']['title']}")
            print(f"      language: {detail['ai_metadata']['language']}  tags: {detail['ai_metadata']['tags']}")
            print(f"      description: {detail['resource']['description'][:160]}")
            s, body = alice.call("POST", f"/api/v1/resources/{resource_id}/process", {})
            check("process again without force -> 409 ALREADY_PROCESSED", s == 409 and error_code(body) == "ALREADY_PROCESSED")
            s, _ = alice.call("POST", f"/api/v1/resources/{resource_id}/process", {"force": True})
            check("force reprocess -> 202", s == 202)
            again = wait_for_terminal(alice, resource_id, args.timeout)
            check("forced reprocess PROCESSED", again is not None and again["processing_status"] == "PROCESSED")

    # 6. isolation and auth
    bob = register(args.base, "bob")
    for label, call in (
        ("detail", lambda: bob.call("GET", f"/api/v1/resources/{resource_id}")),
        ("process", lambda: bob.call("POST", f"/api/v1/resources/{resource_id}/process", {})),
        ("processing", lambda: bob.call("GET", f"/api/v1/resources/{resource_id}/processing")),
    ):
        s, body = call()
        check(f"other user gets 404 on {label}", s == 404 and error_code(body) == "NOT_FOUND", str(s))
    s, _ = Client(args.base).call("GET", f"/api/v1/resources/{resource_id}/processing")
    check("anonymous gets 401", s == 401)

    # 7. cleanup
    if not args.keep:
        for rid in ids | {resource_id}:
            s, _ = alice.call("DELETE", f"/api/v1/resources/{rid}")
            check(f"delete resource {rid[:8]}", s == 204, str(s))
        s, _ = alice.call("GET", f"/api/v1/resources/{resource_id}")
        check("deleted resource is gone (404)", s == 404)

    print("\nFAILED: " + ", ".join(failures) if failures else "\nAll checks passed.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()