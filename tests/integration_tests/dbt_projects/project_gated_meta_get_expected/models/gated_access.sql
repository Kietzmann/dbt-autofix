{{ config(
    materialized='view', 
    meta={'access_test_key': 'sql_value'}
) }}

-- Same-file custom key must be moved to meta before get -> meta_get is allowed
select '{{ config.meta_get("access_test_key") }}' as g
