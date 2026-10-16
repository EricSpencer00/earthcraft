/** Validate producer states before deciding whether there is anything to upload. */
export function exportProgress(record, limit) {
 if (!record || !Number.isInteger(record.tiles) || !Array.isArray(record.deferred) ||
     record.deferred.length>2*limit || !Number.isInteger(record.inspected) ||
     record.inspected<0 || record.inspected>2*limit) throw Error('Invalid bounded exporter progress');
 if (record.no_upgrades!==undefined && (!Array.isArray(record.no_upgrades) || record.no_upgrades.length>2*limit))
  throw Error('Invalid bounded no-upgrade progress');
 if (record.state==='busy') return {state:'blocked',phase:'export_busy',reason:String(record.reason??'Exporter busy').slice(0,500)};
 if (!Number.isInteger(record.eligible_unarchived) || record.eligible_unarchived<0 ||
     typeof record.scan_complete!=='boolean') throw Error('Invalid exporter coverage progress');
 const progress={export_state:record.state,eligible_unarchived:record.eligible_unarchived,
  inspected:record.inspected,deferred:record.deferred,no_upgrades:record.no_upgrades??[],scan_complete:record.scan_complete};
 if (record.state==='caught_up' && record.tiles===0 && record.eligible_unarchived===0 &&
     record.scan_complete && record.deferred.length===0) return {...progress,state:'caught_up'};
 if ((record.state==='deferred'||record.state==='scan_incomplete') && record.tiles===0 &&
     record.eligible_unarchived>0) return {...progress,state:'waiting_on_export'};
 if (record.state==='ready' && record.tiles>=1 && record.tiles<=limit &&
     Array.isArray(record.entries) && record.entries.length===record.tiles &&
     record.eligible_unarchived>=record.tiles) return {...progress,state:'running'};
 throw Error('Exporter state and tile count disagree');
}
