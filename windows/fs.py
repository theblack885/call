import datetime

import requests


def enc(v):
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {k: enc(x) for k, x in v.items()}}}
    if isinstance(v, datetime.datetime):
        u = v.astimezone(datetime.timezone.utc)
        return {"timestampValue": u.strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
    raise TypeError(f"unsupported: {type(v)}")


def dec(v):
    if "stringValue" in v:
        return v["stringValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "booleanValue" in v:
        return v["booleanValue"]
    if "timestampValue" in v:
        return v["timestampValue"]
    if "mapValue" in v:
        return {k: dec(x) for k, x in v["mapValue"].get("fields", {}).items()}
    return None


class Firestore:
    """Firebase Firestore se REST ke zariye baat (login ke bina, khule rules ke saath)."""

    def __init__(self, project, key=""):
        self.base = f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents"
        self.key = key
        self.s = requests.Session()

    def _params(self, extra=None):
        p = list(extra or [])
        if self.key:
            p.append(("key", self.key))
        return p

    @staticmethod
    def _check(r):
        if r.status_code >= 300:
            try:
                msg = r.json()["error"]["message"]
            except Exception:
                msg = r.text[:200]
            raise RuntimeError(f"Firebase {r.status_code}: {msg}")
        return r

    @staticmethod
    def _doc(d):
        f = {k: dec(v) for k, v in d.get("fields", {}).items()}
        f["_id"] = d["name"].split("/")[-1]
        return f

    def create(self, collection, fields):
        body = {"fields": {k: enc(v) for k, v in fields.items()}}
        r = self._check(self.s.post(f"{self.base}/{collection}", params=self._params(), json=body, timeout=15))
        return r.json()["name"].split("/")[-1]

    def get(self, path):
        r = self.s.get(f"{self.base}/{path}", params=self._params(), timeout=15)
        if r.status_code == 404:
            return None
        self._check(r)
        return self._doc(r.json())

    def patch(self, path, fields):
        params = self._params([("updateMask.fieldPaths", k) for k in fields])
        body = {"fields": {k: enc(v) for k, v in fields.items()}}
        self._check(self.s.patch(f"{self.base}/{path}", params=params, json=body, timeout=15))

    def list(self, path):
        r = self.s.get(f"{self.base}/{path}", params=self._params([("pageSize", "100")]), timeout=15)
        if r.status_code == 404:
            return []
        self._check(r)
        return [self._doc(d) for d in r.json().get("documents", [])]

    def incoming(self, my_id):
        body = {
            "structuredQuery": {
                "from": [{"collectionId": "calls"}],
                "where": {
                    "compositeFilter": {
                        "op": "AND",
                        "filters": [
                            {"fieldFilter": {"field": {"fieldPath": "to"}, "op": "EQUAL",
                                             "value": {"stringValue": my_id}}},
                            {"fieldFilter": {"field": {"fieldPath": "status"}, "op": "EQUAL",
                                             "value": {"stringValue": "ringing"}}},
                        ],
                    }
                },
                "limit": 5,
            }
        }
        r = self._check(self.s.post(f"{self.base}:runQuery", params=self._params(), json=body, timeout=15))
        out = []
        for item in r.json():
            d = item.get("document")
            if d:
                out.append(self._doc(d))
        return out
