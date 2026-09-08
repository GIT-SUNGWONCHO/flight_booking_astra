"""Change date through the live strip, requiring fresh API evidence, without reload."""
import time


def refresh_departure(page, anchor, timeout_ms=8000):
    started=time.monotonic()
    clicked=page.evaluate("""anchor => {
      const U=KE_UTIL,P=KE_PROBE,shown=U.searchedDate(),api=P.shownDate();
      if(!U.onDeparture() || !shown || (api&&shown!==api) || shown===anchor)
        return {ok:false,why:'invalid-refresh-start'};
      const r=U.findStripDate(anchor);
      if(!r?.el || !r.selectable || !U.hittable(r.el)) return {ok:false,why:'refresh-date-unavailable'};
      const since=Date.now(); U.fireClick(r.el); return {ok:true,since,fromDate:shown};
    }""",anchor)
    if not clicked['ok']:
        return clicked
    try:
        page.wait_for_function("""({anchor,since}) => KE_PROBE.shownDate(since)===anchor &&
          KE_UTIL.searchedDate()===anchor""",arg={'anchor':anchor,'since':clicked['since']},timeout=timeout_ms)
    except Exception:
        return {'ok':False,'why':'refresh-response-not-confirmed'}
    return {'ok':True,'anchor':anchor,'seconds':round(time.monotonic()-started,3)}


def fire_departure_live(page, target):
    return page.evaluate("""target => {
      const R=window.KE_REC,U=window.KE_UTIL,P=window.KE_PROBE,H=window.KE_HUD;
      if(!R||!U||!P||!U.onDeparture()||U.loggedOut()) return {ok:false,why:'not-ready'};
      const shown=U.searchedDate(), api=P.shownDate();
      if(!shown || (api&&api!==shown)) return {ok:false,why:'date-evidence-mismatch'};
      if(shown===target) return {ok:false,why:'same-date-would-reuse-stale-seats'};
      const strip=U.findStripDate(target), from=R.departureStep();
      if(!strip?.el || from<0) return {ok:false,why:'target-not-in-strip'};
      // Not selectable yet is allowed: recorder waits for the real UI transition.
      H.state.armed=false; H.save();
      R.state.startedAt=0; R.state.playAfterReload=false;
      R.reset(from,target); R.play();
      return {ok:true,fromDate:shown,target,selectableAtFire:strip.selectable};
    }""", target)
