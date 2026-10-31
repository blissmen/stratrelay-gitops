BEGIN;
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='17335fb0-2fbe-5a7a-9e93-c3e2170ef208'::uuid AND attributes->>'orchestrator_run_id'='mt5-daily-20261031t180907-5ec8c13';
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='f7cd1d50-476e-50cd-b247-e1d9dd01f093'::uuid AND attributes->>'orchestrator_run_id'='mt5-daily-20261031t180907-5ec8c13';
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='f205586d-3164-5bcf-bbaf-52316af4133e'::uuid AND attributes->>'orchestrator_run_id'='mt5-daily-20261031t180907-5ec8c13';
COMMIT;
