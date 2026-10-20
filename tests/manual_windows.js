/* Historical, opt-in probes against a locally installed app.
 * These are not the isolated regression suite in tests/browser.
 * They may change UI preferences, read local metadata and save screenshots.
 * Some assertions target older feature versions. Review a probe before use.
 */
if(process.env.VAMPM_MANUAL_WINDOWS_TEST!=='1'){
  throw new Error('Manual local-library probe disabled. Review the script first, then explicitly set VAMPM_MANUAL_WINDOWS_TEST=1. Use npm run test:ui for isolated regression tests.');
}
if(process.platform!=='win32')throw new Error('This manual probe requires Windows.');
exports.sample=(name)=>{
  const value=process.env[name];
  if(!value)throw new Error('Set '+name+' to a sample from your own disposable test library.');
  return value;
};
