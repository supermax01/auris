// Actual comparison functions and FICTIONAL A/B answers, entirely offline.
const assert = require('node:assert/strict');
class Element {
  constructor(tag) {this.tag=tag;this.children=[];this.dataset={};this.text='';}
  set textContent(value) {this.text=String(value);this.children=[];}
  get textContent() {return this.text+this.children.map(n=>n.textContent).join(' ');}
  append(...nodes) {this.children.push(...nodes);}
}
const document={createElement:tag=>new Element(tag)};
const session={fields:fixture.fields};
const turns=fixture.turns;
/* COMPARISON FUNCTIONS */
function descendants(node,tag){
  return node.children.flatMap(child=>[...(child.tag===tag?[child]:[]),...descendants(child,tag)]);
}
function leaves(value,path=''){
  if(value!==null&&typeof value==='object'&&Object.keys(value).length){
    return Object.entries(value).flatMap(([key,child])=>leaves(child,path?path+'.'+key:key));
  }
  return [[path,value]];
}
for(const task of fixture.tasks){
  const table=answerPreview(task);
  assert.equal(table.tag,'table');
  assert.equal(table.className,'comparison');
  assert.equal(table.tabIndex,0); // Focus the table to scroll its containing pane by keyboard.
  const headers=descendants(descendants(table,'thead')[0],'th');
  assert.equal(headers.length,3);
  assert.ok(headers.every(cell=>cell.scope==='col'));
  const rows=descendants(descendants(table,'tbody')[0],'tr');
  for(const row of rows){
    assert.equal(row.children[0].scope,'row');
    assert.equal(row.children.length,3);
    assert.ok(!row.hidden);
    const different=row.children[1].textContent!==row.children[2].textContent;
    assert.equal(row.className==='different',different);
    assert.equal(row.children[0].textContent.includes('— differs'),different);
  }
  for(const [index,side] of ['a','b'].entries()){
    const answer=task[side];
    if(answer===null){
      assert.ok(headers[index+1].textContent.includes('No fact'));
      assert.ok(rows.every(row=>row.children[index+1].textContent==='Not present'));
      continue;
    }
    if(typeof answer==='string'){
      assert.equal(rows.find(row=>row.dataset.field==='source_speaker_role').children[index+1].textContent,answer);
      assert.equal(rows.find(row=>row.dataset.field==='quote').children[index+1].textContent,turns.find(t=>t.id===task.segment_id).text);
      continue;
    }
    // Enumerate the actual serialized fields, including nested/empty/null values.
    for(const [path,value] of leaves(answer)){
      const row=rows.find(row=>row.dataset.field===path);
      assert.ok(row,`Dropped ${side}.${path}`);
      assert.equal(row.children[index+1].textContent,comparisonValue(value));
    }
    for(const [spanIndex,span] of answer.evidence.entries()){
      const row=rows.find(row=>row.dataset.field===`evidence.${spanIndex}.quote`);
      const quote=Array.from(turns.find(turn=>turn.id===span.segment_id).text).slice(span.char_start,span.char_end).join('');
      assert.equal(row.children[index+1].textContent,quote);
      assert.ok(quote.includes('café')); // Unicode is retained exactly.
    }
    const fieldNames=rows.map(row=>row.dataset.field);
    const core=['kind','concept_text','polarity','experiencer','temporality'];
    if(answer.kind)assert.deepEqual(fieldNames.slice(0,core.length),core);
    const firstEvidence=fieldNames.findIndex(key=>key.startsWith('evidence.'));
    assert.ok(fieldNames.slice(firstEvidence).every(key=>key.startsWith('evidence.')));
  }
}
const base=fixture.tasks.find(task=>task.category==='fact'&&task.a&&task.b);
// Render unknown future fields too, without requiring a client schema change.
const future=structuredClone(base);
future.a.future_field={text:'FICTIONAL extra field <inert>',values:[false,0,'']};
const futureRows=descendants(answerPreview(future),'tr');
assert.ok(futureRows.some(row=>row.dataset.field==='future_field.text'&&row.children[1].textContent==='FICTIONAL extra field <inert>'));
const differing=descendants(answerPreview(base),'tr').filter(row=>row.className==='different');
assert.ok(differing.some(row=>row.dataset.field==='temporality'));
assert.ok(!differing.some(row=>row.dataset.field==='kind'));
