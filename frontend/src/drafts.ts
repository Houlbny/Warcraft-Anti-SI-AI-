/** Each task in each adventure keeps a separate local draft. */
export function draftKey(adventureId:string,taskId:string):string{return `adventure-draft:${encodeURIComponent(adventureId)}:${encodeURIComponent(taskId)}`}

/** A completed save may clear only the exact snapshot it wrote. */
export function clearSavedDraft(storage:Pick<Storage,'getItem'|'removeItem'>,key:string,saved:string){if(storage.getItem(key)===saved)storage.removeItem(key)}
