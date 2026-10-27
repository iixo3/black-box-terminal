/* Own desktop wheel routing so a browser's latched event target cannot keep
 * receiving fresh gestures after the pointer moves to another column.
 * Platforms without a momentum flag require an explicit, best-effort fallback.
 * A bounded easing redistributes each input over a few frames without adding
 * travel distance. Touch and keyboard stay native.
 */
(function (root) {
  'use strict';
  function createRouter() {
    let owner = null, previousTime = -Infinity, previousDelta = 0;
    return {
      reset() { owner = null; previousTime = -Infinity; previousDelta = 0; },
      route({ hovered, time, delta, momentum, discrete = false }) {
        const gap = time - previousTime;
        const reversed = delta * previousDelta < 0;
        const renewed = Math.abs(delta) > Math.abs(previousDelta) * 1.5 + 2;
        // A true platform momentum flag is authoritative. Without one, a
        // pause, reversal or renewed impulse is evidence of a new gesture.
        const fresh = momentum === false || (momentum === undefined &&
          (gap > 90 || reversed || renewed || discrete));
        if (!owner || (hovered !== owner && fresh)) owner = hovered;
        previousTime = time;
        previousDelta = delta;
        return owner;
      }
    };
  }

  function createScroller({requestFrame, cancelFrame, reducedMotion}) {
    const pending = new Map();
    let frame = null, previousTime = null;
    const clamp = (element,value) => Math.max(0,Math.min(value,element.scrollHeight-element.clientHeight));
    function stop() {
      if (frame !== null) cancelFrame(frame);
      frame = null; previousTime = null; pending.clear();
    }
    function tick(time) {
      frame = null;
      const elapsed = previousTime === null ? 16 : Math.min(50,Math.max(0,time-previousTime));
      previousTime = time;
      for (const [element, state] of pending) {
        if (!element.isConnected) { pending.delete(element); continue; }
        state.target = clamp(element,state.target);
        const remaining = state.target-element.scrollTop;
        const step = remaining*(1-Math.exp(-elapsed/55));
        element.scrollTop = Math.abs(step)<.5 ? state.target : element.scrollTop+step;
        if (Math.abs(state.target-element.scrollTop)<.5) {
          element.scrollTop=state.target; pending.delete(element);
        }
      }
      if (pending.size) frame=requestFrame(tick);
      else previousTime=null;
    }
    return {
      stop,
      push(element,delta) {
        const distance=delta*.6;
        if (reducedMotion()) { stop(); element.scrollTop=clamp(element,element.scrollTop+distance); return; }
        const state=pending.get(element) || {target:element.scrollTop,direction:Math.sign(delta)};
        // Discard outstanding travel on reversal so scrolling never fights input.
        if (Math.sign(delta)!==state.direction) state.target=element.scrollTop;
        state.direction=Math.sign(delta);
        state.target=clamp(element,state.target+distance);
        pending.set(element,state);
        if (frame===null) frame=requestFrame(tick);
      }
    };
  }

  function install(main, reader) {
    const router = createRouter();
    const scroller = createScroller({requestFrame:requestAnimationFrame,cancelFrame:cancelAnimationFrame,
      reducedMotion:()=>matchMedia('(prefers-reduced-motion:reduce)').matches});
    const reset = () => { router.reset(); scroller.stop(); };
    let pointer = null;
    const columnAt = point => document.elementFromPoint(point.x, point.y)
      ?.closest('.news-column')?.querySelector('.column-feed');
    document.addEventListener('pointermove', event => {
      if (event.pointerType === 'mouse') pointer = { x:event.clientX, y:event.clientY };
    }, { passive:true });
    window.addEventListener('blur', () => { pointer = null; reset(); });
    window.addEventListener('resize', reset);
    document.addEventListener('keydown', reset, {capture:true});
    document.addEventListener('pointerdown', reset, {capture:true});
    document.addEventListener('visibilitychange', reset);
    document.addEventListener('wheel', event => {
      // Never intercept pinch zoom, horizontal gestures, the reader or phone UI.
      if (event.ctrlKey || event.metaKey || event.shiftKey || reader.open ||
          !matchMedia('(min-width:801px)').matches || !main.querySelector('.columns') ||
          !event.cancelable || !event.deltaY || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
        reset(); return;
      }
      const hovered = columnAt(pointer || { x:event.clientX, y:event.clientY });
      if (!hovered || !main.contains(hovered)) { reset(); return; }
      const factor = event.deltaMode === 1 ? 20 : event.deltaMode === 2 ? hovered.clientHeight : 1;
      const delta = event.deltaY * factor;
      const momentum = typeof event.momentum === 'boolean' ? event.momentum : undefined;
      const destination = router.route({ hovered, time:event.timeStamp, delta, momentum,
        discrete:event.deltaMode !== 0 });
      event.preventDefault();
      if (destination?.isConnected) scroller.push(destination,delta);
    }, { passive:false, capture:true });
    return { reset };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { createRouter, createScroller, install };
  else root.BBT_SCROLL = { install };
})(typeof window === 'undefined' ? null : window);
