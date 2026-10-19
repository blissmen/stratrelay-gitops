BEGIN;
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='a18be906-f63b-5a3c-981b-36e8f3950c99'::uuid AND attributes->>'corrected_run_id'='pa-ticks-20261019-1745-v3';
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='c5d62982-e482-52c1-94b3-93cc0f06ad4d'::uuid AND attributes->>'corrected_run_id'='pa-ticks-20261019-1745-v3';
COMMIT;
