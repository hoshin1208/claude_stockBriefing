"""
kakao_refresh.py
카카오 액세스 토큰을 리프레시 토큰으로 갱신하고
GitHub Actions Secret을 자동 업데이트하는 스크립트.

카카오 액세스 토큰은 6시간마다 만료되므로,
이 스크립트를 별도 Actions workflow로 주기적으로 실행합니다.
"""

import os
import re
import sys
import json
import requests
from base64 import b64encode
from nacl import encoding, public

KAKAO_REST_API_KEY  = os.environ["KAKAO_REST_API_KEY"]
KAKAO_REFRESH_TOKEN = os.environ["KAKAO_REFRESH_TOKEN"]
GH_TOKEN            = os.environ["GH_PAT"]
GH_REPO             = os.environ["GH_REPO"]


def refresh_kakao_token():
    resp = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type":    "refresh_token",
            "client_id":     KAKAO_REST_API_KEY,
            "refresh_token": KAKAO_REFRESH_TOKEN,
        },
    )
    data = resp.json()
    if "access_token" not in data:
        print(f"❌ 토큰 갱신 실패: {data}")
        sys.exit(1)
    print("✅ 카카오 액세스 토큰 갱신 성공")
    return data["access_token"], data.get("refresh_token")


def get_public_key():
    resp = requests.get(
        f"https://api.github.com/repos/{GH_REPO}/actions/secrets/public-key",
        headers={"Authorization": f"Bearer {GH_TOKEN}"},
    )
    print(f"  공개키 API 응답: {resp.status_code} {resp.text}")  # ← 이 줄 추가
    return resp.json()

def encrypt_secret(public_key_value: str, secret_value: str) -> str:
    pk  = public.PublicKey(public_key_value.encode(), encoding.Base64Encoder)
    box = public.SealedBox(pk)
    return b64encode(box.encrypt(secret_value.encode())).decode()


def update_github_secret(secret_name: str, secret_value: str, pk_data: dict):
    encrypted = encrypt_secret(pk_data["key"], secret_value)
    resp = requests.put(
        f"https://api.github.com/repos/{GH_REPO}/actions/secrets/{secret_name}",
        headers={"Authorization": f"Bearer {GH_TOKEN}"},
        json={"encrypted_value": encrypted, "key_id": pk_data["key_id"]},
    )
    if resp.status_code in (201, 204):
        print(f"  ✅ GitHub Secret '{secret_name}' 업데이트 완료")
    else:
        print(f"  ❌ GitHub Secret '{secret_name}' 업데이트 실패: {resp.text}")


if __name__ == "__main__":
    new_access, new_refresh = refresh_kakao_token()
    pk_data = get_public_key()

    update_github_secret("KAKAO_ACCESS_TOKEN", new_access, pk_data)

    if new_refresh:
        update_github_secret("KAKAO_REFRESH_TOKEN", new_refresh, pk_data)
        print("  ✅ 리프레시 토큰도 갱신됨")
    else:
        print("  ℹ️ 리프레시 토큰 변동 없음")
