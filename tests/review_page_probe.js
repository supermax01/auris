// Execute the real review page against FICTIONAL API snapshots, offline.
const assert = require('node:assert/strict');
class Element {
  constructor(tag) {this.tag = tag; this.children = []; this.text = ''; this.listeners = {};}
  set textContent(value) {this.text = value ?? ''; this.children = [];}
  get textContent() {return this.text + this.children.map(n => n.textContent).join(' ');}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children = [...nodes];}
  addEventListener(name, callback) {this.listeners[name] = callback;}
  focus() {}
}
const nodes = new Map();
const document = {
  createElement: tag => new Element(tag),
  getElementById: id => {
    if (!nodes.has(id)) nodes.set(id, new Element('div'));
    return nodes.get(id);
  },
};
const location = {hash: '#token=fictional'};
let snapshotIndex = 0;
const fetch = async () => ({ok: true, json: async () => snapshots[snapshotIndex]});

/* PAGE SCRIPT */

function descendants(node, tag) {
  return node.children.flatMap(child => [
    ...(child.tag === tag ? [child] : []), ...descendants(child, tag),
  ]);
}
function visibleText(node) {
  if (node.hidden) return '';
  const children = node.tag === 'details' && !node.open
    ? node.children.filter(child => child.tag === 'summary') : node.children;
  return node.text + children.map(visibleText).join(' ');
}
(async () => {
  const statuses = [
    'Final export blocked: 7 items block export',
    'Final export blocked: 1 item blocks export',
    snapshots[2].ready_message,
  ];
  for (snapshotIndex = 0; snapshotIndex < snapshots.length; snapshotIndex++) {
    await load();
    const snapshot = snapshots[snapshotIndex];
    assert.equal($('error').textContent, '');
    assert.equal($('status').textContent, statuses[snapshotIndex]);
    if (!snapshotIndex) {
      assert.ok(descendants($('records'), 'h2').some(n => n.textContent === 'Needs review (10)'));
    }
    assert.ok(descendants($('outputs'), 'a').some(a => a.title === 'Download rewritten bundle.json'));
    const cards = descendants($('records'), 'article');
    assert.equal(cards.length, snapshot.items.length);
    for (const item of snapshot.items) {
      const article = cards.find(n => n.children[0].id === 'item-' + item.id);
      assert.ok(article);
      const badges = article.children.filter(n => n.className === 'badge');
      assert.equal(badges.length, snapshot.export.blocking_ids.includes(item.id) ? 1 : 0);
      if (badges.length) assert.equal(visibleText(badges[0]), 'blocks final export');
      for (const evidence of snapshot.evidence[item.id]) {
        assert.ok(article.children.some(n => n.tag === 'blockquote' && n.textContent === evidence.quote));
        assert.ok(visibleText(article).includes(evidence.quote));
        assert.ok(article.children.some(n => n.textContent === evidence.receipt));
        if (evidence.alignment === 'disfluency-v2') {
          assert.ok(visibleText(article).includes('matched ignoring fillers/repeats'));
        }
      }
      for (const context of article.children.filter(n => n.tag === 'details')) {
        assert.ok(!context.open);
        assert.equal(descendants(context, 'blockquote').length, 0);
        assert.ok(context.children[0].textContent.startsWith('Transcript context'));
        assert.ok(descendants(context, 'mark').length);
        assert.ok(!visibleText(context).includes(descendants(context, 'mark')[0].textContent));
      }
      const row = snapshot.review.find(row => row.id === item.id);
      if (row.editable) {
        const editor = descendants(article, 'form')[0];
        assert.ok(editor.hidden);
        const toggle = descendants(article, 'button').find(button => button.textContent === 'Correct fact');
        toggle.listeners.click();
        assert.equal(editor.hidden, false);
        const kind = descendants(editor, 'select')[0];
        assert.equal(kind.value, snapshot.assertions[item.id].kind);
        for (const control of [...descendants(editor, 'input'), ...descendants(editor, 'textarea')]) {
          assert.equal(control.maxLength, 4000);
        }
        kind.value = 'explicit_new_order'; kind.listeners.change();
        assert.equal(descendants(editor, 'input').length, 4); // concept, dose, route, frequency
        assert.equal(descendants(editor, 'select').length, 5); // kind and four classifications
        assert.ok(!visibleText(editor).includes('Evidence quote'));
      }
      if (row.state === 'approve') {
        const buttons = descendants(article, 'button').map(button => button.textContent);
        assert.ok(!buttons.includes('Approve'));
        assert.ok(buttons.includes('Dismiss') && buttons.includes('Undo latest action'));
        if (row.editable) assert.ok(buttons.includes('Correct fact'));
      }
    }
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
