BEGIN;
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='90aff4a4-00d4-531c-867e-041ab9ca27c0'::uuid AND attributes->>'orchestrator_run_id'='swing-position-20261028-v1';
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='bf9b8397-c8c1-510e-89cf-f34b5ceb07fb'::uuid AND attributes->>'orchestrator_run_id'='swing-position-20261028-v1';
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='311b6def-4f5e-58e7-bead-64046188ecf2'::uuid AND attributes->>'orchestrator_run_id'='swing-position-20261028-v1';
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='5896fa23-3a64-5525-9ff7-a39ca606ef35'::uuid AND attributes->>'orchestrator_run_id'='swing-position-20261028-v1';
UPDATE strategy_mgmt.strategy_instance_v2 SET online=false,updated_at=now() WHERE id='dd4a4433-d3e2-5fdf-a55c-31f20381e022'::uuid AND attributes->>'orchestrator_run_id'='swing-position-20261028-v1';
COMMIT;
