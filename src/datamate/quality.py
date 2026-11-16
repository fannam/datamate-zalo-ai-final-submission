"""Loss-aware source normalization. Never consults questions or reference answers."""
from __future__ import annotations


def inspect_and_recover(connection, catalog):
    def quoted(name):
        return '"'+name.replace('"','""')+'"'

    report={'policy':'Original files unchanged; retain raw shadows and provenance. Never infer missing business values from gold.','issues':[],'transformations':[],'rows':sum(t['rows'] for t in catalog['tables'])}
    for table in catalog['tables']:
        for column in table['columns']:
            errors=column.get('parse_errors',0)
            if not errors:
                continue
            nonempty=column['nonempty']
            issue={'table':table['name'],'column':column['name'],'kind':column['kind'],'nonempty':nonempty,'parse_errors':errors,'fraction':errors/nonempty if nonempty else 0}
            # An inferred numeric type must not erase an entire categorical column.
            # Preserve it as text, exposing the original values to downstream tools.
            if column['kind']=='number' and errors==nonempty and nonempty:
                connection.execute(f"ALTER TABLE {quoted(table['name'])} ALTER COLUMN {quoted(column['name'])} TYPE VARCHAR USING {quoted(column['name']+'__raw')}")
                column.update(kind='text',parse_errors=0,recovered_from_kind='number',original_parse_errors=errors)
                report['transformations'].append({**issue,'action':'restore_original_text','reason':'All nonempty values failed numeric conversion; preserve rather than replace with NULL.'})
            else:
                issue['severity']='critical' if nonempty and errors/nonempty>=.1 else 'warning'
                raw=quoted(column['name']+'__raw')
                samples=connection.execute(f"SELECT _source_file,_source_sheet,_source_row,{raw} FROM {quoted(table['name'])} WHERE {quoted(column['name'])} IS NULL AND NULLIF(TRIM({raw}),'') IS NOT NULL LIMIT 5").fetchall()
                issue['examples']=[{'file':r[0],'sheet':r[1],'row':r[2],'raw':r[3]} for r in samples]
                report['issues'].append(issue)
    report['critical_count']=sum(i.get('severity')=='critical' for i in report['issues'])
    return report
