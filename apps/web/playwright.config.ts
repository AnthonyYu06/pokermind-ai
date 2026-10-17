import {defineConfig} from '@playwright/test';
export default defineConfig({
 testDir: './e2e', workers: 1, timeout: 30_000, retries: 0,
 use: {baseURL:'http://127.0.0.1:8019',channel:'chromium',locale:'en-US',trace:'retain-on-failure'},
 projects: [
  {name:'desktop',use:{viewport:{width:1280,height:900}}},
  {name:'mobile',use:{viewport:{width:390,height:844}}}
 ],
 webServer: {command:`${process.env.POKERMIND_TEST_PYTHON||'../../.venv/bin/python'} ../../tests/e2e/serve.py`,url:'http://127.0.0.1:8019/api/health',reuseExistingServer:false,timeout:30_000}
});
