/* Folder uploads stream File objects; no full-file buffering in browser memory. */
window.VaMPMImports={
  selection(files,options){
    const allowed=new Set(options.extensions),selected=[];let packages=0;
    for(const file of files){
      const ext='.'+file.name.split('.').pop().toLowerCase();
      if(!allowed.has(ext))continue;
      if(ext==='.var'){
        if(!/^.+\..+\.\d+\.var$/i.test(file.name))continue;
        packages++;
      }else if(!file.webkitRelativePath)continue;
      selected.push(file);
    }
    return {files:selected,packages,attachments:selected.length-packages,ignored:files.length-selected.length};
  },
  upload(file,token,progress){
    return new Promise((resolve,reject)=>{
      const q=new URLSearchParams({name:file.name});
      if(file.webkitRelativePath)q.set('relative_path',file.webkitRelativePath);
      const xhr=new XMLHttpRequest();xhr.open('POST','/api/upload?'+q);
      xhr.setRequestHeader('X-Session-Token',token);if(window.VaMPMWorkspace?.epoch)xhr.setRequestHeader('X-Workspace-Epoch',window.VaMPMWorkspace.epoch);xhr.setRequestHeader('Content-Type','application/octet-stream');
      xhr.upload.onprogress=e=>progress(Math.min(e.loaded,file.size));
      xhr.onerror=()=>reject(Error(tr('文件上传失败，请检查包管家是否仍在运行。')));
      xhr.onload=()=>{
        try{const out=JSON.parse(xhr.responseText);if(out.workspace_changed)window.VaMPMWorkspace?.reload();if(xhr.status!==200)throw Error(msg(out.error)||tr('文件上传失败'));resolve(out);}
        catch(e){reject(e);}
      };
      xhr.send(file);
    });
  }
};
