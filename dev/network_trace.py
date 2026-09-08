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


class NetworkTrace:
    def __init__(self, page, folder, target):
        self.folder, self.target = folder, target.replace("-", "")
        self.rows = []
        self.order_created = False
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
            if path.endswith('/inputTravellers') and response.ok:
                data = response.json()
                # Only the documented response field; never log order IDs.
                self.order_created = order_id_observed(data)
                row['orderCreated'] = self.order_created
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

    def save(self):
        atomic_json(self.folder / "network.json", {"runId": run_id(), "rows": self.rows[-500:]})
