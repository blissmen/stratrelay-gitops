BEGIN;
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='23c971c9-cbf9-5539-99e9-0ebd1366065d'::uuid AND attributes->>'orchestrator_run_id'='trendline-20261102t020642-47b08ed';
COMMIT;
