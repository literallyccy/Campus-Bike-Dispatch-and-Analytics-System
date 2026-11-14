const fs=require('fs'),assert=require('assert');
const model=require('../dashboard/v1/review.js');
const row=(served,unmet,pending=0)=>({potential:served+unmet+pending,served,unmet,pending_walks:pending});
const checks=[];
function test(name,fn){fn();checks.push(name);}
test('positive improvement',()=>assert.equal(model.verdict(row(80,20),row(90,10)).tone,'benefit'));
test('negative outcome retained',()=>assert.equal(model.verdict(row(90,10),row(80,20)).tone,'loss'));
test('zero improvement is neutral',()=>assert.equal(model.verdict(row(90,10),row(90,10)).tone,'neutral'));
test('different pending outcomes cannot imply improvement',()=>assert.equal(model.verdict(row(80,20),row(80,10,10)).tone,'neutral'));
test('zero demand avoids division',()=>assert.equal(model.verdict(row(0,0),row(0,0)).tone,'neutral'));
test('mismatched denominators rejected',()=>assert.equal(model.verdict(row(80,20),row(90,20)).tone,'neutral'));
test('inconsistent counts rejected',()=>assert.equal(model.valid({...row(80,20),potential:99}),false));
test('only incremental cost attributed to new plan',()=>assert.deepEqual(model.extraCost({moved:10,transport_km:2,work_minutes:5},{moved:15,transport_km:3,work_minutes:9}),{moved:5,transport_km:1,work_minutes:4}));
const output=process.argv[2];fs.writeFileSync(output,JSON.stringify({passed:true,checks,scope:'Decision wording only; browser interactions checked separately'},null,2));console.log(JSON.stringify({passed:true,checks}));
