def model(dbt, session):
    dbt.config(materialized="view", access_py_key="py_value")
    v = dbt.config.get("access_py_key")
    return session.sql(f"select '{v}' as c")
