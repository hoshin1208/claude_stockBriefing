"""
kakao_refresh.py
카카오 액세스 토큰을 리프레시 토큰으로 갱신하고
GitHub Actions Secret을 자동 업데이트하는 스크립트.

카카오 액세스 토큰은 6시간마다 만료되므로,
이 스크립트를 별도 Actions workflow로 주기적으로 실행합니다.
"""

import os
import requests

KAKAO_REST_API_KEY   = os.environ["KAKAO_REST_API_KEY"]
KAKAO_REFRESH_TOKEN  = os.environ["KAKAO_REFRESH_TOKEN"]
GH_TOKEN             = os.environ["GH_TOKEN"]          # repo secret 업데이트용
GH_REPO              = os.environ["GH_REPO"]            # "username/repo-name"


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
        raise RuntimeError(f"토큰 갱신 실패: {data}")
    return data["access_token"], data.get("refresh_token")


def update_github_secret(secret_name: str, value: str):
    """GitHub REST API로 Actions Secret 업데이트"""
    # 공개 키 가져오기
    pk_resp = requests.get(
        f"https://api.github.com/repos/{GH_REPO}/actions/secrets/public-key",
        headers={"Authorization": f"Bearer {GH_TOKEN}"},
    )
    pk_data = pk_resp.json()

    # nacl로 암호화
    from base64 import b64encode
    from nacl import encoding, public

    pk      = public.PublicKey(pk_data["key"].encode(), encoding.Base64Encoder)
    box     = public.SealedBox(pk)
    enc_val = b64encode(box.encrypt(value.encode())).decode()

    # Secret 업데이트
    requests.put(
        f"https://api.github.com/repos/{GH_REPO}/actions/secrets/{secret_name}",
        headers={"Authorization": f"Bearer {GH_TOKEN}"},
        json={"encrypted_value": enc_val, "key_id": pk_data["key_id"]},
    )
    print(f"  ✅ GitHub Secret '{secret_name}' 업데이트 완료")


if __name__ == "__main__":
    print("카카오 토큰 갱신 중...")
    new_access, new_refresh = refresh_kakao_token()

    update_github_secret("KAKAO_ACCESS_TOKEN", new_access)
    if new_refresh:
        update_github_secret("KAKAO_REFRESH_TOKEN", new_refresh)

    print("완료!")
