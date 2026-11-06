import {useRef, type ChangeEvent, type InputHTMLAttributes} from 'react';

export default function MaterialPicker({files,onChange,disabled=false}:{files:File[];onChange:(files:File[])=>void;disabled?:boolean}){
 const fileInput=useRef<HTMLInputElement>(null);
 const folderInput=useRef<HTMLInputElement>(null);
 const select=(event:ChangeEvent<HTMLInputElement>)=>{
  const selected=Array.from(event.currentTarget.files||[]);
  event.currentTarget.value='';
  if(selected.length)onChange(selected);
 };
 return <section className="material-picker" aria-label="选择导入材料">
  <div className="button-row">
   <button type="button" disabled={disabled} onClick={()=>fileInput.current?.click()}>导入文件</button>
   <button type="button" disabled={disabled} onClick={()=>folderInput.current?.click()}>导入文件夹</button>
  </div>
  <input ref={fileInput} type="file" multiple hidden tabIndex={-1} disabled={disabled} aria-label="选择文件" onChange={select}/>
  <input ref={folderInput} type="file" multiple hidden tabIndex={-1} disabled={disabled} aria-label="选择文件夹" {...({webkitdirectory:''} as InputHTMLAttributes<HTMLInputElement>)} onChange={select}/>
  <p className="muted material-picker-help">文件可多选，文件夹会保留目录结构。重新选择会替换当前列表。</p>
  <div className="material-selection">
   <p role="status">{files.length?`已选择 ${files.length} 个文件`:'尚未选择文件或文件夹'}</p>
   {files.length>0&&<>
    <ul aria-label="已选材料">{files.map((file,index)=><li key={`${file.webkitRelativePath||file.name}:${index}`}>{file.webkitRelativePath||file.name}</li>)}</ul>
    <div className="material-selection-actions"><small className="muted">点击下方“导入材料”开始导入。</small><button type="button" disabled={disabled} onClick={()=>onChange([])}>清除选择</button></div>
   </>}
  </div>
 </section>;
}
