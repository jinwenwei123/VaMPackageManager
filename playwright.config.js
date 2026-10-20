const {defineConfig}=require('@playwright/test');
module.exports=defineConfig({
  testDir:'tests/browser',timeout:60000,workers:1,reporter:'list',
  use:{headless:true,viewport:{width:1440,height:1000},screenshot:'only-on-failure'},
  webServer:{command:'python3 tests/ui_server.py',url:'http://127.0.0.1:47851/',reuseExistingServer:false,timeout:30000},
  outputDir:'test-results/playwright'
});
