"""앱 재구성 동안 관측된 예매 변경 API를 차단하는 인계 관찰기.

생성/시작만으로 이동·상태 쓰기·결제를 하지 않는다. 같은 참조 판정은 전체 화면
검증이 아니며, 이후 동의/결제 단계까지 감시 수명을 유지해야 한다.
"""
from urllib.parse import urlsplit
import time
import handoff
from site_drive import HandoffWatch


class BridgeGuard(HandoffWatch):
    def stop(self):
        super().stop()
        self._started = None

    @staticmethod
    def _is_order(url):
        # 부모 라우팅은 동일 핸들러로 모든 주문/선택 경로를 차단한다.
        return urlsplit(url).path in (handoff.ORDER_PATH, *handoff.SELECTION_PATHS)

    def inspect_gate(self, *, reference, ordered_at, expected_url, timeout_ms=5000):
        """이미 시작한 guard로 이동 완료 후 참조를 관찰한다. 이동 자체는 호출자 책임.

        URL·참조는 필요조건이다. 검증된 모델/회원/여정/금액 및 실제 결제창 판정은
        호출자가 수행해야 한다. 성공 후에도 stop하지 않아 후발 요청을 감시한다.
        """
        if self.started is None or self._page is None:
            raise ValueError('guard-not-started')
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 0<=timeout_ms<=30000:
            raise ValueError('invalid-timeout')
        deadline=time.monotonic()+timeout_ms/1000
        while True:
            if self._page.is_closed():
                return {'stage':'page-closed','matched':False}
            if self._page.url != expected_url:
                return {'stage':'unexpected-page','matched':False}
            verdict=self.judge(reference,ordered_at)
            # request 이벤트가 route.abort 완료보다 먼저 전달될 수 있다. 실제 미차단과
            # 아직 차단 결과가 나오지 않은 상태를 즉시 같은 것으로 보고하지 않는다.
            if verdict.state not in ('reference-unobserved','new-order-requested') or time.monotonic()>=deadline:
                return {'stage':verdict.state,'matched':verdict.same_reference,
                        'orderRequests':verdict.order_requests,
                        'unblockedOrders':verdict.order_requests_unblocked,
                        'selectionRequests':verdict.selection_requests}
            self._page.wait_for_timeout(min(50,max(1,int((deadline-time.monotonic())*1000))))
