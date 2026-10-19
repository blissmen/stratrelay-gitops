BEGIN;
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='9b6b913d-347d-5f90-b670-e6221a92b25c'::uuid AND attributes->>'corrected_run_id'='pa-ticks-20261019-1700-v1';
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='d1fc279e-b8f0-5d48-a173-14a22fa3ded2'::uuid AND attributes->>'corrected_run_id'='pa-ticks-20261019-1700-v1';
COMMIT;
