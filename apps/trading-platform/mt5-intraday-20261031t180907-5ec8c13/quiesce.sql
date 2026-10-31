BEGIN;
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='3c035613-255f-5bf4-9308-b2bf83b80f82'::uuid AND attributes->>'orchestrator_run_id'='mt5-intraday-20261031t180907-5ec8c13';
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='3fbc6e8f-0dca-525b-833b-b782b7ac914b'::uuid AND attributes->>'orchestrator_run_id'='mt5-intraday-20261031t180907-5ec8c13';
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='178c7c6d-5fe9-50f3-9be7-e2e2759b8b10'::uuid AND attributes->>'orchestrator_run_id'='mt5-intraday-20261031t180907-5ec8c13';
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='b0c95322-51fa-5c0f-a964-a7b9861b5c3b'::uuid AND attributes->>'orchestrator_run_id'='mt5-intraday-20261031t180907-5ec8c13';
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='9ebf2433-8232-5185-816e-f13e0017b7db'::uuid AND attributes->>'orchestrator_run_id'='mt5-intraday-20261031t180907-5ec8c13';
COMMIT;
