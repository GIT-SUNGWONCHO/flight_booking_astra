"""토큰 원문·회원 정보는 출력하지 않고 만료 시각만 진단한다."""
import base64
import json
from datetime import datetime,timezone
from urllib.parse import unquote


def session_health(context, opening_epoch):
    try:
        cookies=context.cookies(['https://www.koreanair.com'])
        token_cookie=next((c for c in cookies if c['name']=='T'),None)
        if not token_cookie:return {'known':False,'reason':'token-cookie-not-found'}
        token=json.loads(unquote(token_cookie['value'])).get('access_token','')
        part=token.split('.')[1]
        payload=json.loads(base64.urlsafe_b64decode(part+'='*(-len(part)%4)))
        expires=float(payload['exp'])
        return {'known':True,'expiresAt':datetime.fromtimestamp(expires,timezone.utc).isoformat(),
                'secondsAfterOpen':round(expires-opening_epoch),'coversOpen':expires>=opening_epoch+180}
    except Exception:return {'known':False,'reason':'expiry-schema-unrecognized'}
