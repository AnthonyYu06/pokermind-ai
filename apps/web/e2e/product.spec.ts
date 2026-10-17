import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';

test('full-hand self-play opponent exposes a legal policy and resumes',async({page},info)=>{
 test.skip(!process.env.POKERMIND_NFSP_SOURCE,'Requires the explicitly supplied local research artifact');
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/');await page.getByRole('button',{name:'Hold’em practice',exact:true}).click();
 await page.getByLabel('Holdem opponent').selectOption('selfplay');
 await expect(page.getByRole('button',{name:'Start a hand',exact:true})).toBeEnabled();
 await page.getByRole('button',{name:'Start a hand',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Your practice table',exact:true})).toBeVisible();
 if(await page.getByRole('button',{name:'Inspect policy mix',exact:true}).isVisible()){
  await page.getByRole('button',{name:'Inspect policy mix',exact:true}).click();
  await expect(page.getByLabel('Learned action probabilities')).toContainText('not win probabilities');
  await page.getByRole('button',{name:'Let the bot play this turn',exact:true}).click();
 }
 await page.reload();await page.getByRole('button',{name:'Hold’em practice',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Your practice table',exact:true})).toBeVisible();
 await expect(page.getByRole('region',{name:'Self-play research evidence'})).toContainText('18,000 training hands');
 await expect(page.getByRole('region',{name:'Self-play research evidence'})).toContainText('General Hold’em optimality is unproven');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
 await page.evaluate(()=>scrollTo(0,0));
 await page.screenshot({path:`../../docs/screenshots/holdem-selfplay-${info.project.name}.png`,fullPage:true});
 expect(errors).toEqual([]);
});

test('practice coach compares generated actions and preserves a drill',async({page},info)=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/');await page.getByRole('button',{name:'AI practice coach',exact:true}).click();
 await page.getByRole('button',{name:'Deal a river hand',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Your river decision',exact:true})).toBeVisible();
 await expect(page.getByLabel('Hidden card')).toHaveCount(2);
 await page.getByRole('button',{name:'Compare all actions',exact:true}).click();
 const comparison=page.getByRole('region',{name:'Model action comparison'});
 await expect(comparison).toContainText('All 990 legal opponent hands');
 await expect(comparison).toContainText('Best estimate');
 await expect(comparison).toContainText('it never folds or raises');
 if(process.env.POKERMIND_COACH_REPORT){
  await expect(page.locator('.coach-controls')).toContainText('ML ESTIMATE');
  await page.getByText('Inspect the model evidence',{exact:true}).click();
  await expect(comparison).toContainText('3.26 percentage points');
 }else await expect(page.locator('.coach-controls')).toContainText('EXACT REFERENCE ONLY');
 const previous=await comparison.innerText();
 await page.reload();await page.getByRole('button',{name:'AI practice coach',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Why the model favors this choice',exact:true})).toBeVisible();
 if(process.env.POKERMIND_COACH_REPORT)await page.getByText('Inspect the model evidence',{exact:true}).click();
 expect(await comparison.innerText()).toEqual(previous);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
 await page.evaluate(()=>scrollTo(0,0));
 await page.screenshot({path:`../../docs/screenshots/river-coach-${process.env.POKERMIND_COACH_REPORT?'neural':'reference'}-${info.project.name}.png`,fullPage:true});
 await page.getByRole('button',{name:'Next practice hand',exact:true}).click();
 await expect(page.getByRole('button',{name:'Compare all actions',exact:true})).toBeVisible();
 expect(errors).toEqual([]);
});

test('measured neural evidence is visible without horizontal page overflow',async({page},info)=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/');
 await page.getByRole('button',{name:'Research lab',exact:true}).click();
 const heading=page.getByRole('heading',{name:'Fresh river equity research'});
 await expect(heading).toBeVisible();
 const section=page.locator('section').filter({has:heading});
 await expect(section).toContainText('15,998 versioned batch predictions');
 await expect(section).toContainText('3.26 ± 0.06 pp');
 await expect(section).toContainText('baseline gain: passed');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
 await page.screenshot({path:`../../docs/screenshots/river-v3-${info.project.name}.png`,fullPage:true});
 expect(errors).toEqual([]);
});

test('static file preview commits and reports real provenance',async({page},info)=>{
 await page.goto('/');await page.getByRole('button',{name:'Imports',exact:true}).click();
 const fixture=readFileSync('../../tests/fixtures/synthetic/sample.jsonl');
 const suffix='\n'.repeat(info.project.name==='desktop'?2:3);
 await page.locator('input[type=file]').setInputFiles({name:`browser-${info.project.name}.jsonl`,mimeType:'text/plain',buffer:Buffer.concat([fixture,Buffer.from(suffix)])});
 await expect(page.getByRole('button',{name:'Commit import',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Commit import',exact:true}).click();
 await expect(page.locator('section.report')).toContainText('committed · real provenance');
 await page.getByLabel('Diagnostic status').selectOption('failed');
 await expect(page.locator('section.report')).toContainText('0 matching records');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
});
