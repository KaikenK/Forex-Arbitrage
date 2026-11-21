-- C45 (RESEARCH_TRACKER §6, 2026-11-21): the DEVIATION 4 extraction, unchanged except for the period.
-- Source GKG 2.1 English stream, partitions 2025-05-01..2026-10-31 inclusive; ts = DATE parsed as UTC;
-- url = DocumentIdentifier; domain = SourceCommonName; title = <PAGE_TITLE> from Extras; rows kept if the
-- lower-cased title matches the DEVIATION 4 recall filter; non-partitioned, clustered by month (the
-- sandbox expires partitions older than 60 days). The 2 Nov statement was typed in the console and not
-- saved; this text is rebuilt from the logged specification.
CREATE SCHEMA IF NOT EXISTS `arbex-c45.gdelt_fx` OPTIONS (location = 'US');

CREATE TABLE `arbex-c45.gdelt_fx.fx_headlines`
CLUSTER BY month AS
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
  AND REGEXP_CONTAINS(LOWER(title), r'\b(dollar|greenback|euro|eurozone|euro zone|sterling|pound|yen|swiss franc|rupee|forex|exchange rate|currency|currencies|federal reserve|fed|fomc|ecb|bank of england|boe|bank of japan|boj|snb|swiss national bank|rbi|reserve bank of india|lagarde|powell)\b');

-- Unfiltered rows per UTC day, for the DEVIATION 5 gap rule (b); from one month earlier for the
-- trailing 28-day median.
CREATE TABLE `arbex-c45.gdelt_fx.daily_counts` AS
SELECT DATE(PARSE_TIMESTAMP('%Y%m%d%H%M%S', CAST(DATE AS STRING))) AS day, COUNT(*) AS n
FROM `gdelt-bq.gdeltv2.gkg_partitioned`
WHERE _PARTITIONTIME >= TIMESTAMP('2025-04-01') AND _PARTITIONTIME < TIMESTAMP('2026-11-01')
GROUP BY day;
