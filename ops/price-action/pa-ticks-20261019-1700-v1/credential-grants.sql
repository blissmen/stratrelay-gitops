-- Controlled admin path only; no password or Kubernetes Secret is stored here.
-- Set login credentials through the established secure provisioning path before rollout.
BEGIN;
SET LOCAL statement_timeout='15s';
DO $pa$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='price_action_corrected_observer') THEN
  CREATE ROLE price_action_corrected_observer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='price_action_corrected_observer'
   AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls)) THEN
  RAISE EXCEPTION 'Existing observer role is privileged; stop and inspect';
 END IF;
END $pa$;
GRANT USAGE ON SCHEMA research,strategy_mgmt TO price_action_corrected_observer;
GRANT SELECT ON strategy_mgmt.strategy_instance_v2,strategy_mgmt.strategy_version,
 strategy_mgmt.parameter_set TO price_action_corrected_observer;
GRANT SELECT,INSERT,UPDATE ON research.price_action_observer_state TO price_action_corrected_observer;
GRANT SELECT,INSERT ON research.price_action_observer_evaluation TO price_action_corrected_observer;
DO $pa$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
   AND c.relkind IN ('r','p','v','m','f')
   AND (n.nspname||'.'||c.relname) NOT IN ('research.price_action_observer_state','research.price_action_observer_evaluation')
   AND has_table_privilege('price_action_corrected_observer',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')) THEN
  RAISE EXCEPTION 'Existing observer role can write outside PA research tables';
 END IF;
 EXECUTE format('GRANT CONNECT ON DATABASE %I TO price_action_corrected_observer',current_database());
END $pa$;
COMMIT;
