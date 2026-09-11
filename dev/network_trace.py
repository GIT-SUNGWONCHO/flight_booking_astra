"""Persist browser network timings and seat projections, never credentials/bodies."""
import json
from urllib.parse import urlsplit
from runtime import atomic_json, run_id


def order_id_observed(data):
    # Fail closed if the response schema changes. A nested error/echo payload
    # containing an old identifier must never count as a new order response.
    return (isinstance(data, dict) and isinstance(data.get('orderId'), str)
            and bool(data['orderId'].strip())
            and not data.get('error') and not data.get('errorCode'))


def order_response_evidence(data):
    """본문 원문 대신 허용 필드의 구조만 기록한다. 업무 수락을 임의로 판정하지 않는다."""
    allowed={'orderId','order','orderInfo','reservation','reservationId','reservationInfo',
             'reservationNumber','bookingReference','booking','pnr','recordLocator','pageTicket',
             'data','result','response','success','status','code','error','errorCode','errors',
             'message','payment','paymentInfo','paymentId','travellers','travelers','travellerList',
             'travelerList','flightList','boundList','fare','fareInformation','totalAmount','currency'}
    def structure(value,depth=0):
        if depth>3:return {'type':'truncated'}
        if isinstance(value,dict):
            return {'type':'object','fields':{k:structure(v,depth+1) for k,v in value.items() if k in allowed},
                    'omittedFieldCount':sum(k not in allowed for k in value)}
        if isinstance(value,list):return {'type':'array','count':len(value),'items':[structure(v,depth+1) for v in value[:2]]}
        return {'type':'null' if value is None else 'boolean' if isinstance(value,bool)
                else 'number' if isinstance(value,(float,int)) else 'string'}
    return {'acceptance':'unverified','orderIdObserved':order_id_observed(data),
            'responseStructure':structure(data),'seatHoldVerified':False,
            'interpretation':'응답 필드 관측만 기록. 필드 없음은 주문 실패, HTTP200은 업무 수락으로 해석하지 않음.'}


class NetworkTrace:
    def __init__(self, page, folder, target):
        self.folder, self.target = folder, target.replace("-", "")
        self.rows = []
        self.order_created = False
        self.order_evidence = {'acceptance':'unverified','reason':'승객 전송 응답 미관측','seatHoldVerified':False}
        page.on("requestfinished", self.finished)
        page.on("requestfailed", self.failed)

    def failed(self, request):
        if "/api/" not in request.url:
            return
        self.rows.append({"path": urlsplit(request.url).path, "failure": request.failure})
        self.save()

    def finished(self, request):
        path = urlsplit(request.url).path
        if "/api/" not in path:
            return
        try:
            response = request.response()
            row = {"path": path, "method": request.method, "status": response.status,
                   "timing": request.timing}
            if path.endswith('/inputTravellers'):
                data = response.json()
                self.order_evidence = {**order_response_evidence(data),'httpStatus':response.status}
                # 과거 실행기 호환 필드다. 업무 수락·좌석 확보의 판정값으로 사용하지 않는다.
                self.order_created = response.ok and order_id_observed(data)
                row['orderCreated'] = self.order_created
                row['orderEvidence'] = self.order_evidence
            if path.endswith("/awardAvailability") and response.ok:
                data = response.json()
                flights = []
                for bound in data.get("upsellBoundAvailList", []):
                    for f in bound.get("availFlightList", []):
                        date = str(f.get("departureDate", ""))[:8]
                        if not date.endswith(self.target):
                            continue
                        info = (f.get("flightInfoList") or [{}])[0]
                        flights.append({"date": date, "origin": f.get("departureAirport"),
                            "destination": f.get("arrivalAirport"), "carrier": info.get("operationCarrierCode"),
                            "codeShare": info.get("codeShare"),
                            "fares": [{"family": c.get("fareFamily"), "seats": c.get("seatCount"), "soldout": c.get("soldout")}
                                      for c in f.get("commercialFareFamilyList", [])]})
                row["flights"] = flights
            self.rows.append(row)
            self.save()
        except Exception as e:
            self.rows.append({"path": path, "traceError": type(e).__name__})
            self.save()

    def save(self):
        atomic_json(self.folder / "network.json", {"runId": run_id(), "rows": self.rows[-500:]})
