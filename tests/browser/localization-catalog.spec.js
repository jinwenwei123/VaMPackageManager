const {test,expect}=require('@playwright/test');
const fs=require('fs'),path=require('path'),vm=require('vm');
const {babelParse,traverse}=require(path.resolve('node_modules/playwright/lib/transform/babelBundle.js'));

test('every UI template has an English translation and interpolation never translates data',()=>{
  const sandbox={NodeFilter:{SHOW_TEXT:4},document:{documentElement:{},body:{},title:'VaM 包管家',
    createTreeWalker:()=>({nextNode:()=>false}),querySelectorAll:()=>[]}};
  sandbox.window=sandbox;sandbox.VaMPMLanguageConfig={language:'en'};
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync('web/locales/en.js','utf8'),sandbox);
  vm.runInContext(fs.readFileSync('web/i18n.js','utf8'),sandbox);
  const i18n=sandbox.VaMPMI18n;
  let checked=0;
  for(const file of fs.readdirSync('web').filter(name=>name.endsWith('.js')&&name!=='i18n.js')){
    const ast=babelParse(fs.readFileSync('web/'+file,'utf8'),file);
    traverse(ast,{
      CallExpression({node}){
        if(!['tr','ui'].includes(node.callee.name)||node.arguments[0]?.type!=='StringLiteral')return;
        i18n[node.callee.name](node.arguments[0].value);checked++;
      },
      TaggedTemplateExpression({node}){
        if(!['tr','ui'].includes(node.tag.name))return;
        const source=node.quasi.quasis.map((part,i)=>part.value.cooked+(i<node.quasi.expressions.length?'{'+i+'}':'')).join('');
        i18n[node.tag.name](source);checked++;
      }
    });
  }
  expect(checked).toBeGreaterThan(700);
  expect([...i18n.missing]).toEqual([]);
  const raw='中文文件夹/主页.{0}.$&.var';
  expect(i18n.tr(['选择 ',''],raw)).toBe('Select '+raw);
  expect(i18n.ui(['<button title="打开 ','">查看详情</button>'],raw))
    .toBe('<button title="Open '+raw+'">View details</button>');
  expect(i18n.message('文件变化，请重新生成预览：'+raw))
    .toBe('File changed. Generate another preview: '+raw);
  expect(i18n.message(raw)).toBe(raw);
});
