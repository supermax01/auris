// Real review functions against FICTIONAL state; no browser, sockets or packages.
const assert = require('node:assert/strict');
class Element {
  constructor(tag) {this.tag = tag; this.children = []; this.text = ''; this.listeners = {};}
  set textContent(value) {this.text = value ?? ''; this.children = [];}
  get textContent() {return this.text + this.children.map(n => n.textContent).join(' ');}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children = [...nodes];}
  addEventListener(name, fn) {this.listeners[name] = fn;}
  focus() {}
}
const nodes = new Map();
const document = {
  createElement: tag => new Element(tag),
  getElementById: id => {if (!nodes.has(id)) nodes.set(id, new Element('div')); return nodes.get(id);},
};
const location = {hash:'#token=fictional'};
function descendants(node, tag) {
  return node.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...descendants(child, tag)]);
}
function visibleText(node) {
  if (node.hidden) return '';
  const children = node.tag === 'details' && !node.open ? node.children.filter(c => c.tag === 'summary') : node.children;
  return node.text + children.map(visibleText).join(' ');
}
/* PAGE SCRIPT */
state = snapshot;
for (const [code, text] of Object.entries(state.reason_labels)) {
  const message = validatorMessage({code:'consistency', details:{text:code}});
  assert.equal(message.textContent, text);
  assert.ok(message.title.includes(code));
}
const issues = ['error','fatal','warning','information'].map(severity => ({severity,code:'invariant',details:{text:`FICTIONAL ${severity}`}}));
for (const laneState of ['PASS','NOT_CHECKED','FAIL']) {
  const lane = laneView('base_r4', {state:laneState,issues});
  assert.ok(visibleText(lane).includes('FICTIONAL error'));
  assert.ok(visibleText(lane).includes('FICTIONAL fatal'));
  const disclosures = descendants(lane, 'details');
  if (laneState === 'FAIL') {
    assert.equal(disclosures.length, 0);
    assert.ok(visibleText(lane).includes('FICTIONAL warning'));
    assert.ok(visibleText(lane).includes('FICTIONAL information'));
  } else {
    assert.equal(disclosures.length, 1);
    assert.ok(!disclosures[0].open);
    assert.equal(disclosures[0].children[0].textContent, 'Validator notes (2)');
    assert.ok(!visibleText(lane).includes('FICTIONAL warning'));
    assert.ok(!visibleText(lane).includes('FICTIONAL information'));
    disclosures[0].open = true;
    assert.ok(visibleText(lane).includes('FICTIONAL warning'));
    assert.ok(visibleText(lane).includes('FICTIONAL information'));
  }
}
assert.equal(descendants(laneView('base_r4', {state:'PASS',issues:[]}), 'details').length, 0);
assert.ok(validatorMessage({severity:'error',code:'invariant'}).title.includes('invariant'));
render();
const text = visibleText($('records'));
assert.ok(text.includes(state.reason_labels.value_text_number_not_in_quote));
assert.ok(!text.includes('value_text_number_not_in_quote'));
assert.ok(text.includes(state.reason_labels.quote_not_found));
assert.ok(!text.includes('quote_not_found'));
assert.ok(text.includes(state.reason_labels.union_tag_invalid));
assert.ok(!text.includes('union_tag_invalid'));
for (const code of ['value_text_number_not_in_quote','quote_not_found','union_tag_invalid']) {
  assert.ok([...descendants($('records'), 'p'), ...descendants($('records'), 'li')].some(n => n.title?.includes(code)));
}
