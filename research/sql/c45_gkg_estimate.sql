-- Size estimate only (dry run): the SELECT of c45_gkg_extract.sql's fx_headlines table, unchanged.
SELECT DATE_TRUNC(DATE(ts), MONTH) AS month, ts, url, domain, title
FROM (
  SELECT
    PARSE_TIMESTAMP('%Y%m%d%H%M%S', CAST(DATE AS STRING)) AS ts,
    DocumentIdentifier AS url,
    SourceCommonName AS domain,
    REGEXP_EXTRACT(Extras, r'<PAGE_TITLE>(.*?)</PAGE_TITLE>') AS title
  FROM `gdelt-bq.gdeltv2.gkg_partitioned`
  WHERE _PARTITIONTIME >= TIMESTAMP('2025-05-01') AND _PARTITIONTIME < TIMESTAMP('2026-11-01')
)
WHERE title IS NOT NULL
  AND REGEXP_CONTAINS(LOWER(title), r'\b(dollar|greenback|euro|eurozone|euro zone|sterling|pound|yen|swiss franc|rupee|forex|exchange rate|currency|currencies|federal reserve|fed|fomc|ecb|bank of england|boe|bank of japan|boj|snb|swiss national bank|rbi|reserve bank of india|lagarde|powell)\b')
