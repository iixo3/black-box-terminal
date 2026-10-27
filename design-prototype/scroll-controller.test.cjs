const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = { module:{exports:{}} };
vm.runInNewContext(fs.readFileSync(`${__dirname}/scroll-controller.js`, 'utf8'), context);
const { createRouter, createScroller } = context.module.exports;

test('moving the pointer alone does not transfer a decaying tail from A into B', () => {
  const router = createRouter();
  const events = [
    {hovered:'A',time:0,delta:80}, {hovered:'A',time:16,delta:60},
    {hovered:'B',time:32,delta:40}, {hovered:'B',time:48,delta:26},
    {hovered:'B',time:64,delta:18}
  ];
  assert.deepEqual(events.map(event=>router.route(event)), ['A','A','A','A','A']);
});
test('a fresh impulse over B takes over while A still has residual momentum', () => {
  const router = createRouter();
  router.route({hovered:'A',time:0,delta:25});
  router.route({hovered:'B',time:16,delta:15});
  assert.equal(router.route({hovered:'B',time:32,delta:55}), 'B');
  assert.equal(router.route({hovered:'B',time:48,delta:30}), 'B');
});
test('fresh reverse-direction input over B takes over immediately', () => {
  const router = createRouter();
  router.route({hovered:'A',time:0,delta:25});
  assert.equal(router.route({hovered:'B',time:16,delta:-2}), 'B');
});
test('slow input after a pause can start B without an acceleration spike', () => {
  const router = createRouter();
  router.route({hovered:'A',time:0,delta:25});
  assert.equal(router.route({hovered:'B',time:160,delta:2}), 'B');
});
test('platform momentum wins over acceleration and timing heuristics', () => {
  const router = createRouter();
  router.route({hovered:'A',time:0,delta:5,momentum:false});
  assert.equal(router.route({hovered:'B',time:200,delta:30,momentum:true}), 'A');
  assert.equal(router.route({hovered:'B',time:216,delta:1,momentum:false}), 'B');
});
test('resetting after a view/filter change does not retain a removed column', () => {
  const router = createRouter();
  router.route({hovered:'A',time:0,delta:30});
  router.reset();
  assert.equal(router.route({hovered:'B',time:16,delta:2}), 'B');
});
test('conventional discrete wheel input follows the current column', () => {
  const router = createRouter();
  router.route({hovered:'A',time:0,delta:60,discrete:true});
  assert.equal(router.route({hovered:'B',time:16,delta:60,discrete:true}), 'B');
});

function harness() {
  const handlers = {}, A = {scrollTop:0,isConnected:true,clientHeight:400,scrollHeight:2000},
    B = {scrollTop:0,isConnected:true,clientHeight:400,scrollHeight:2000};
  const columns = [A,B];
  const clock=frameClock();
  let desktop = true;
  const sandbox = {
    module:{exports:{}}, window:{addEventListener(){}},
    requestAnimationFrame:clock.requestFrame,cancelAnimationFrame:clock.cancelFrame,
    matchMedia:query=>({matches:query.includes('min-width')?desktop:false}),
    document:{
      addEventListener:(name,handler)=>{handlers[name]=handler;},
      elementFromPoint:x=>({closest:()=>({querySelector:()=>columns[x<100?0:1]})})
    }
  };
  vm.runInNewContext(fs.readFileSync(`${__dirname}/scroll-controller.js`, 'utf8'),sandbox);
  const reader={open:false}, main={querySelector:()=>true,contains:e=>columns.includes(e)};
  sandbox.module.exports.install(main,reader);
  function wheel(overrides={}) {
    let prevented=false;
    handlers.wheel({clientX:10,clientY:10,timeStamp:0,deltaX:0,deltaY:40,deltaMode:0,
      cancelable:true,preventDefault(){prevented=true;},...overrides});
    return prevented;
  }
  return {A,B,reader,wheel,flush:clock.flush,phone(){desktop=false;},
    point(x){handlers.pointermove({pointerType:'mouse',clientX:x,clientY:10});}};
}
test('DOM adapter follows pointer even when browser wheel coordinates remain latched to A', () => {
  const h=harness(); h.point(10); h.wheel();
  h.point(150); h.wheel({timeStamp:16,deltaY:20});
  h.flush(); assert.equal(h.A.scrollTop,36); assert.equal(h.B.scrollTop,0);
  h.wheel({timeStamp:32,deltaY:70});h.flush();
  assert.equal(h.A.scrollTop,36); assert.equal(h.B.scrollTop,42);
});
test('pinch zoom, horizontal movement and noncancelable events remain native', () => {
  const h=harness();
  assert.equal(h.wheel({ctrlKey:true}),false);
  assert.equal(h.wheel({deltaX:70}),false);
  assert.equal(h.wheel({cancelable:false}),false);
  assert.equal(h.A.scrollTop,0);
});

function frameClock() {
  let next=0,time=0;const frames=new Map();
  const step=()=>{time+=16;const work=[...frames.values()];frames.clear();work.forEach(fn=>fn(time));};
  return {requestFrame:fn=>{frames.set(++next,fn);return next;},cancelFrame:id=>frames.delete(id),step,
    flush(){let count=0;while(frames.size&&count++<100)step();assert.equal(frames.size,0,'animation must settle');return count;},
    pending:()=>frames.size};
}
function smoothHarness(reduce=false) {
  const clock=frameClock();
  const scroller=createScroller({...clock,reducedMotion:()=>reduce});
  const element=()=>({scrollTop:100,scrollHeight:2000,clientHeight:400,isConnected:true});
  return {clock,scroller,A:element(),B:element()};
}
test('slower travel eases over frames and stops without adding momentum distance',()=>{
  const {clock,scroller,A}=smoothHarness();scroller.push(A,100);
  assert.equal(A.scrollTop,100);clock.step();
  assert.ok(A.scrollTop>100&&A.scrollTop<160);
  assert.ok(clock.flush()<20,'a single gesture should settle within 320ms');
  assert.equal(A.scrollTop,160);
});
test('A and B retain independent easing targets during an immediate handoff',()=>{
  const {clock,scroller,A,B}=smoothHarness();scroller.push(A,100);clock.step();
  scroller.push(B,50);clock.step();assert.ok(B.scrollTop>100);
  clock.flush();assert.equal(A.scrollTop,160);assert.equal(B.scrollTop,130);
});
test('reversal cancels pending forward travel instead of fighting the new gesture',()=>{
  const {clock,scroller,A}=smoothHarness();scroller.push(A,100);clock.step();
  const turn=A.scrollTop;scroller.push(A,-10);clock.step();
  assert.ok(A.scrollTop<turn);clock.flush();assert.equal(A.scrollTop,turn-6);
});
test('hitting an edge cannot accumulate travel that delays a reverse gesture',()=>{
  const {clock,scroller,A}=smoothHarness();A.scrollTop=1590;scroller.push(A,2000);clock.flush();
  assert.equal(A.scrollTop,1600);scroller.push(A,-100);clock.flush();assert.equal(A.scrollTop,1540);
});
test('reduced motion applies slower distance immediately without animation',()=>{
  const {clock,scroller,A}=smoothHarness(true);scroller.push(A,100);
  assert.equal(A.scrollTop,160);assert.equal(clock.pending(),0);
});
test('keyboard/view/reader interruption discards pending travel',()=>{
  const {clock,scroller,A}=smoothHarness();scroller.push(A,100);clock.step();
  const stopped=A.scrollTop;scroller.stop();clock.flush();assert.equal(A.scrollTop,stopped);
});
test('pixel-rounded scroll positions still settle rather than scheduling forever',()=>{
  const {clock,scroller,A}=smoothHarness();let top=100;
  Object.defineProperty(A,'scrollTop',{get:()=>top,set:value=>{top=Math.round(value);}});
  scroller.push(A,101);clock.flush();assert.equal(A.scrollTop,161);
});
test('article reader and phone scrolling remain native', () => {
  const h=harness(); h.reader.open=true;
  assert.equal(h.wheel(),false);
  h.reader.open=false; h.phone();
  assert.equal(h.wheel(),false);
  assert.equal(h.A.scrollTop,0);
});
