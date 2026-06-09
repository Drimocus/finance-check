create table wallet_journal
(
    id              bigint unsigned not null    primary key,
    corporation_id  int unsigned    not null,
    ref_type        varchar(128)    not null,
    journal_date    datetime        not null,
    description     varchar(4096)   not null,
    amount          bigint          null,
    reason          varchar(4096)   null,
    first_party_id  int unsigned    null,
    second_party_id int unsigned    null,
    context_id_type varchar(128)    null,
    context_id      bigint unsigned null,
    constraint wallet_journal_id_unique    unique (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
create index wallet_journal_corporation_id_index on wallet_journal (corporation_id);
create index wallet_journal_date_index on wallet_journal (journal_date);
create index wallet_journal_ref_type_index on wallet_journal (ref_type);

create table corporations
(
    id                int               not null   primary key,
    corporation_name  varchar(255)      null,
    character_id      int               not null,
    last_journal_date datetime          null,
    active            tinyint default 1 null,
    constraint corporations_id_unique   unique (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- 2022-07-07
create index wallet_journal_ref_amount_index on wallet_journal (amount);

-- 2023-06-23, journal_year_month = yyyymm digit: 202512, 202601, 202602, etc
ALTER TABLE wallet_journal ADD journal_year_month INT unsigned NOT NULL after journal_date;
CREATE index wallet_journal_year_month_index on wallet_journal (journal_year_month);
UPDATE wallet_journal SET journal_year_month = (YEAR(journal_date) * 100) + MONTH(journal_date);

-- 2026-05-13, track all divisions, so we can see payments from other divisions.
ALTER TABLE wallet_journal ADD division TINYINT unsigned NOT NULL after context_id;
UPDATE wallet_journal SET division = 1 WHERE division = 0;
CREATE index wallet_journal_division_index on wallet_journal (division);

-- 2026-05-14 add extra corp info columns, useful info for filtering / grouping
-- and automatic contacting with evemail / possible future slack integration.
-- allowing NULL, no edits to web gui code to add/edit corps needed for now.
ALTER TABLE corporations ADD is_alt_corp BOOLEAN NULL DEFAULT 0 after id;
-- current ingame ceo character, automated from ESI, stored to save api calls.
ALTER TABLE corporations ADD corporation_ceo_id BIGINT unsigned NULL after corporation_name;
-- main character responsible for corp, may be equal to ceo id.
ALTER TABLE corporations ADD corporation_owner_id BIGINT unsigned NULL after corporation_name;

-- 2026-05-14, add tax records table
-- isk amounts sourced from wallet_journal table, limited to same integer data.
CREATE TABLE tax_records
(
    corporation_id      BIGINT unsigned  not null,
    tax_month_date      datetime         not null,
    taxable_income      BIGINT           not null,
    corp_tax_amount     BIGINT           not null,
    brave_tax_amount    BIGINT           not null,
    brave_tax_payments  BIGINT           not null,
    PRIMARY KEY (corporation_id, tax_month_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2026-05-29, add alliance of corporation
ALTER TABLE corporations ADD alliance_id BIGINT unsigned NOT NULL after id;
-- 2026-05-30, add tax toggle for corporation
ALTER TABLE corporations ADD is_taxed BOOLEAN NULL DEFAULT 1 after is_alt_corp;
-- corp without key can be fine if inactive or not yet configured
ALTER TABLE corporations MODIFY character_id int NULL;
-- store so we can avoid esi lookups on page reloads
ALTER TABLE corporations ADD is_want BOOLEAN NULL DEFAULT 0 after id;