with

raw_txns as (
    select
        instruct_hdr_fid,
        instruct_from_fro_msisdn    as msisdn,
        instruct_amount,
        'OUT'                       as direction
    from feeds.fin_log_cfw
    where tbl_dt between cast(date_format(date_add('day', -89, date_parse(cast(20260930 as varchar), '%Y%m%d')), '%Y%m%d') as int)
                     and 20260930
      and status_code = 'EXECUTED'
      and hdr_type = 'RESERVATION'
      and instruct_from_fro_msisdn is not null
      and instruct_from_fro_user_prf in (
            select profile_name from analytics.mobile_money_profiles where subscriber_flag = true
          )

    union all

    select
        instruct_hdr_fid,
        instruct_to_fro_msisdn      as msisdn,
        instruct_amount,
        'IN'                        as direction
    from feeds.fin_log_cfw
    where tbl_dt between cast(date_format(date_add('day', -89, date_parse(cast(20260930 as varchar), '%Y%m%d')), '%Y%m%d') as int)
                     and 20260930
      and status_code = 'EXECUTED'
      and hdr_type = 'RESERVATION'
      and instruct_to_fro_msisdn is not null
      and instruct_to_fro_user_prf in (
            select profile_name from analytics.mobile_money_profiles where subscriber_flag = true
          )
),

sub_activity as (
    select
        msisdn,
        count(distinct instruct_hdr_fid)                                    as txn_count,
        sum(case when direction = 'IN'  then instruct_amount else 0 end)    as inflow_amt,
        sum(case when direction = 'OUT' then instruct_amount else 0 end)    as outflow_amt
    from raw_txns
    group by msisdn
),

mau_30 as (
    select distinct msisdn
    from analytics.momo_rge_base_subscribers_v9
    where date_key = 20260930
      and au_30_day_active_flag = 1
),

momo_age_180 as (
    select msisdn
    from (
        select
            msisdn,
            COALESCE(
                TRY(date_parse(registration_date, '%d-%b-%y')),
                TRY(date_parse(activation_date,   '%d-%b-%y'))
            ) as derived_reg_date
        from devdata.account_holder_dump
        where tbl_dt               = 20260930
          and account_type         = 'MOBILE MONEY'
          and accountholder_status = 'ACTIVE'
          and account_status       = 'ACTIVE'
          and profile in (select profile_name from analytics.mobile_money_profiles where subscriber_flag = true)
    )
    where derived_reg_date is not null
      and date_diff('day', derived_reg_date, date_parse(cast(20260930 as varchar), '%Y%m%d')) >= 180
),

derisk_exclusions as (
    select distinct msisdn
    from devdata.account_holder_dump
    where tbl_dt = 20260930
      and profile in ('MTNU Agent Derisk Class', 'MTNU HV Gold Subscriber')
),

active_overdue_60d as (
    select distinct customer_msisdn as msisdn
    from analytics.momo_loan_book_tracker_loan_state_daily
    where date_key      = 20260930
      and outstanding_ugx > 0
      and days_aging    > 60
),

cooling_off as (
    select distinct customer_msisdn as msisdn
    from analytics.momo_loan_book_tracker_xtrafloat_loan_state_daily
    where date_key = (
            select max(date_key)
            from analytics.momo_loan_book_tracker_xtrafloat_loan_state_daily
          )
      and is_principal_settled  = true
      and days_past_due         > 30
      and closure_date          is not null
      and date_parse(cast(20260930 as varchar), '%Y%m%d')
          between closure_date
              and date_add('day', 90, closure_date)
),

eligible as (
    select
        m30.msisdn,
        coalesce(a.txn_count,   0)  as txn_count_90d,
        coalesce(a.inflow_amt,  0)  as inflow_amt_90d,
        coalesce(a.outflow_amt, 0)  as outflow_amt_90d
    from mau_30 m30
    inner join momo_age_180 m180
        on m30.msisdn = m180.msisdn
    inner join sub_activity a
        on m30.msisdn = a.msisdn
    where a.txn_count >= 5
      and (a.inflow_amt + a.outflow_amt) >= 10000
      and m30.msisdn not in (select msisdn from derisk_exclusions)
      and m30.msisdn not in (select msisdn from active_overdue_60d)
      and m30.msisdn not in (select msisdn from cooling_off)
)

select
    msisdn,
    txn_count_90d,
    inflow_amt_90d,
    outflow_amt_90d,
    cast(1 as tinyint)      as eligible_flag,
    current_timestamp       as inserted_dt,
    20260930                as tbl_dt
from eligible
limit 100
