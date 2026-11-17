"""Return conventions ported from mobile/src/domain/returns.ts and ui/reporting.ts."""
import datetime as dt
from decimal import Decimal
import math
import ledger

def annualize(value,days):
    if value is None or days<=0 or value<=-1:return None
    try:r=math.pow(1+float(value),365/days)-1
    except (OverflowError,ValueError):return None
    return r if math.isfinite(r) else None

def modified_dietz(start,end,flows,start_date,end_date):
    days=(dt.date.fromisoformat(end_date)-dt.date.fromisoformat(start_date)).days
    if days<=0 or Decimal(start)<=0 or Decimal(end)<0:return None
    total=Decimal(0);weighted=Decimal(0)
    for flow in flows:
        if not start_date<=flow['date']<=end_date:continue
        amount=Decimal(flow['amount']);elapsed=(dt.date.fromisoformat(flow['date'])-dt.date.fromisoformat(start_date)).days
        total+=amount;weighted+=amount*Decimal(days-elapsed)/Decimal(days)
    denominator=Decimal(start)+weighted
    if denominator<=0:return None
    value=float((Decimal(end)-Decimal(start)-total)/denominator)
    return value if math.isfinite(value) else None

def time_weighted(valuations,flows,start_date,end_date):
    values={v['date']:Decimal(v['value']) for v in valuations}
    if start_date not in values:return None
    dates=sorted({v['date'] for v in valuations if start_date<v['date']<=end_date}|{f['date'] for f in flows if start_date<f['date']<=end_date}|{end_date})
    previous=values[start_date];linked=Decimal(1)
    for day in dates:
        if day not in values:return None
        current=values[day];flow=sum((Decimal(f['amount']) for f in flows if f['date']==day),Decimal(0));before=current-flow
        if current<0 or before<0:return None
        if previous==0:
            if before!=0:return None
        else:linked*=before/previous
        previous=current
    value=float(linked-1)
    return value if math.isfinite(value) else None

def build(data,as_of,cache=None):
    cache=cache if cache is not None else {}
    def snapshot(day):
        if day not in cache:cache[day]=ledger.overview(data,day)
        return cache[day]
    ee=ledger.active_events(data,as_of);accounts={a['id']:a for a in data['accounts']}
    by_account={a:[e for e in ee if e['account_id']==a] for a in accounts}
    closed={}
    for aid,events in by_account.items():
        valuations=[e for e in events if e['kind'] in ('OPENING','VALUATION')]
        if valuations and Decimal(valuations[-1]['amount'])==0:
            last=valuations[-1]
            if not any(e['kind'] in ('CASH_FLOW','BUY','SELL','DIVIDEND_CASH','DIVIDEND_REINVEST','SPLIT') and ledger.boundary(e)>ledger.boundary(last) for e in events):closed[aid]=last['date']
    points={a:[] for a in accounts}
    # Manual valuations retain same-day ordering; derived holdings endpoints are end-of-day snapshots.
    for aid,events in by_account.items():
        for e in events:
            if e['kind'] in ('OPENING','VALUATION'):points[aid].append(dict(date=e['date'],order=e['order'],value=e['amount'],actual=True))
    days=sorted({e['date'] for e in ee}|{q['date'] for q in data['quotes'] if q['date']<=as_of}|{as_of})
    for day in days:
        for row in snapshot(day)['accounts']:
            aid=row['account_id']
            if aid in closed and day>closed[aid]:continue
            if row['mode']=='自动估值':
                points[aid].append(dict(date=day,order=10**18,value=row['value'],actual=not row['price_dates'] or all(qday==day for qday in row['price_dates'])))
    for aid in points:points[aid].sort(key=lambda p:(p['date'],p['order']))
    years=sorted({e['date'][:4] for e in ee},reverse=True)
    periods=[('month','本月',as_of[:7]+'-01',as_of),('year','今年',as_of[:4]+'-01-01',as_of),('since','记账以来','0001-01-01',as_of)]
    periods += [('y'+y,y+'年',y+'-01-01',min(y+'-12-31',as_of)) for y in years]
    scopes=[]
    for currency in sorted({a['currency'] for a in accounts.values()}):
        scopes.append((f'portfolio:{currency}','全部资产',[a for a in accounts if accounts[a]['currency']==currency],currency))
    scopes += [(aid,a['name'],[aid],a['currency']) for aid,a in accounts.items()]
    pending_checkpoints={p.get('extracted',{}).get('checkpoint_id') for p in data['pending'] if not p.get('resolved_by')}
    result=[]
    for scope_id,name,aids,currency in scopes:
        combined=len(aids)>1
        if combined:
            timeline=[]
            for day in days:
                latest=[]
                for aid in aids:
                    p=[p for p in points[aid] if p['date']<=day]
                    if p:latest.append(p[-1])
                if latest:timeline.append(dict(date=day,order=10**18,value=str(sum((Decimal(p['value']) for p in latest),Decimal(0))),actual=all((p['date']==day and p['actual']) or Decimal(p['value'])==0 for p in latest)))
        else:timeline=points[aids[0]]
        rows=[]
        for pid,label,lower,upper in periods:
            pp=[p for p in timeline if lower<=p['date']<=upper]
            row=dict(id=pid,label=label,start=None,end=None,days=0,profit=None,mwr=None,annualized=None,twr=None,twr_annualized=None,reason='',twr_reason='',notes=[])
            if not pp:row['reason']='此区间没有估值记录';rows.append(row);continue
            first,last=pp[0],pp[-1];start,end=first['date'],last['date'];row.update(start=start,end=end,days=(dt.date.fromisoformat(end)-dt.date.fromisoformat(start)).days)
            flows=[]
            for aid in aids:
                aa=by_account[aid]
                if combined:
                    before=[p for p in points[aid] if p['date']<=start];after=[p for p in points[aid] if p['date']<=end]
                    start_key=(before[-1]['date'],before[-1]['order']) if before else (start,-10**18)
                    end_key=(after[-1]['date'],after[-1]['order']) if after else (end,-10**18)
                else:start_key=(start,first['order']);end_key=(end,last['order'])
                flows += [dict(date=e['date'],amount=e['amount'],order=e['order']) for e in aa if e['kind']=='CASH_FLOW' and start_key<ledger.boundary(e)<=end_key and start<=e['date']<=end]
                if combined:
                    flows += [dict(date=e['date'],amount=e['amount'],order=e['order']) for e in aa if e['kind']=='OPENING' and start<e['date']<=end]
            # Reuse the exact interval boundary/flow selection for chart points.
            daily={}
            for point in pp:daily[point['date']]=point
            curve=[]
            for point in daily.values():
                point_flows=[f for f in flows if (f['date'],f.get('order',0))<=(point['date'],point['order'])]
                profit=Decimal(point['value'])-Decimal(first['value'])-sum((Decimal(f['amount']) for f in point_flows),Decimal(0))
                curve.append(dict(date=point['date'],profit=ledger.money(profit),mwr=modified_dietz(first['value'],point['value'],point_flows,start,point['date']),actual=point['actual']))
            row['curve']=curve
            # Net same-day internal transfers at portfolio scope cancel before return calculation.
            grouped={}
            for f in flows:grouped[f['date']]=grouped.get(f['date'],Decimal(0))+Decimal(f['amount'])
            flows=[dict(date=day,amount=str(amount)) for day,amount in grouped.items() if amount]
            valuation_issues=[p for p in data['pending'] if not p.get('resolved_by') and p.get('extracted',{}).get('affects_valuation') and p['extracted'].get('account_id') in aids and start<=p['extracted'].get('date','')<=end]
            uncertain=[e for e in ee if e['account_id'] in aids and e['kind']=='BASIS_CHECKPOINT' and e['id'] in pending_checkpoints and start<e['date']]
            if uncertain:
                row['reason']='历史资金流差额未定位，不能可靠计算此区间收益率';row['twr_reason']=row['reason'];row['curve']=[];rows.append(row);continue
            row['profit']=ledger.money(Decimal(last['value'])-Decimal(first['value'])-sum((Decimal(f['amount']) for f in flows),Decimal(0)))
            if start==end:
                row['mwr']=0.0 if first['value']==last['value'] and not flows else None;row['twr']=row['mwr'];row['reason']='只有同日数据，不能年化'
            else:
                row['mwr']=modified_dietz(first['value'],last['value'],flows,start,end)
                row['annualized']=annualize(row['mwr'],row['days'])
                vals=[dict(date=p['date'],value=p['value']) for p in pp if p['actual']]
                row['twr']=time_weighted(vals,flows,start,end);row['twr_annualized']=annualize(row['twr'],row['days'])
                if not first['actual'] or not last['actual']:
                    row['twr']=None;row['twr_annualized']=None
                if row['mwr'] is None:row['reason']='期初资产或加权投入基数不为正，无法计算'
                if row['twr'] is None:row['twr_reason']='缺少期初、期末或资金流当日的实际估值'
            if valuation_issues:
                row['twr']=None;row['twr_annualized']=None;row['twr_reason']='区间内有原始估值与日志矛盾，时间加权收益率待核对'
                if any(p['extracted']['date'] in (start,end) for p in valuation_issues):
                    row['mwr']=None;row['annualized']=None;row['profit']=None;row['curve']=[];row['reason']='区间端点估值与原始日志矛盾，资金加权收益率待核对'
            if not last['actual']:row['notes'].append('期末使用最近已知报价，行情日期不齐')
            if combined and not first['actual']:row['notes'].append('期初组合估值日期不齐，资金加权结果为参考值')
            if row['days']<365:row['notes'].append('不足一年，年化为折算值')
            rows.append(row)
        cumulative_profit=next((r['profit'] for r in snapshot(as_of)['accounts'] if r['account_id']==scope_id),snapshot(as_of)['totals'].get(currency,{}).get('profit'))
        for row in rows:
            if row['id']=='since' and cumulative_profit is not None:
                row['profit']=cumulative_profit
                row['profit_basis']='当前资产减已核对净投入；收益金额可知不代表资金流时点齐全'
                for point in row.get('curve',[]):
                    snap=snapshot(point['date'])
                    point['profit']=next((a['profit'] for a in snap['accounts'] if a['account_id']==scope_id),snap['totals'].get(currency,{}).get('profit'))
        result.append(dict(id=scope_id,name=name,currency=currency,rows=rows,archived=bool(not combined and accounts[aids[0]].get('archived')),cumulative_profit=cumulative_profit))
    return result
