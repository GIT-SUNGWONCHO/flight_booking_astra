"""Move the observed KRW redraw before fire; never select a fare or create an order."""
import time

DEP = '/booking/select-award-flight/departure'
CAL = '/booking/calendar-fare-bonus'


def prepare_krw(page, target, timeout_ms=30000):
    deadline = time.monotonic() + timeout_ms / 1000
    result = {'changed': False, 'verified': False, 'calendarReturns': 0}

    def left():
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise TimeoutError('KRW preparation deadline exceeded')
        return remaining

    def currency():
        return page.locator('#currencyBtn').inner_text(timeout=left())

    if DEP not in page.url:
        raise ValueError('KRW preparation requires the live departure page')
    # Tomorrow's newly opening date may not yet be selectable. Restore the
    # actual already-open date we were viewing; the fire path changes the date.
    shown = page.evaluate("() => window.KE_UTIL?.searchedDate?.() || null")
    return_date = shown or target
    if 'KRW' not in currency():
        page.locator('#currencyBtn').click(timeout=left())
        # These controls are recorded in steps.json and observed on the live site.
        labels = page.locator('#filter-currency label').filter(has_text='KRW')
        if labels.count() != 1:
            raise ValueError('Expected exactly one KRW option')
        labels.click(timeout=left())
        page.locator('#filter-currency .filter__apply').click(timeout=left())
        result['changed'] = True
        page.wait_for_function("() => location.pathname.includes('/booking/calendar-fare-bonus') || (document.querySelector('#currencyBtn')?.innerText || '').includes('KRW')", timeout=left())
        # The site can return to the calendar when currency changes.
        if CAL in page.url:
            result['calendarReturns'] += 1
            page.wait_for_function("target => window.KE_UTIL && !!KE_UTIL.findOpenDate('dep-fare-', target)", arg=return_date, timeout=left())
            page.evaluate("target => KE_UTIL.fireClick(KE_UTIL.findOpenDate('dep-fare-', target))", return_date)
            page.get_by_text('검색', exact=True).last.click(timeout=left())
            page.wait_for_url('**' + DEP, timeout=left())
    page.wait_for_function("() => location.pathname.includes('/booking/select-award-flight/departure') && (document.querySelector('#currencyBtn')?.innerText || '').includes('KRW')", timeout=left())
    result['verified'] = True
    return result
