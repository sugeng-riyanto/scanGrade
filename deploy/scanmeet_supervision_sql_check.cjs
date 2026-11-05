// Ephemeral PostgreSQL (PGlite), no network, keys, persistent files or Supabase writes.
// Minimal core fixture; the ScanMeet tables/functions below come from real migrations.
const {PGlite}=require('@electric-sql/pglite');
const fs=require('fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
(async()=>{
 const db=new PGlite();
 await db.exec(`CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS;
 CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY,email text);
 CREATE TABLE public.schools(id uuid PRIMARY KEY,name text,npsn text);
 CREATE TABLE public.profiles(id uuid PRIMARY KEY REFERENCES auth.users(id),school_id uuid REFERENCES public.schools(id),role text,full_name text);
 CREATE TABLE public.exams(id uuid PRIMARY KEY,school_id uuid REFERENCES public.schools(id),teacher_id uuid NOT NULL REFERENCES public.profiles(id),title text);`);
 const names=fs.readdirSync(root+'/supabase/migrations').sort();
 for(const prefix of ['070','071','073','074','075','076','077','079','079']){
  const name=names.find(name=>name.startsWith(prefix+'_'));
  try {await db.exec(fs.readFileSync(root+'/supabase/migrations/'+name,'utf8'));console.log('applied '+name)}
  catch(e){console.error('MIGRATION '+name,e.message,e.detail||'',e.where||'');process.exitCode=1;await db.close();return}
 }
 await db.exec('BEGIN');
 try{
  await db.exec(fs.readFileSync(root+'/tests/scanmeet_supervision_sql_checks.sql','utf8'));
  console.log('PASS supervision SQL assertions');
 }catch(e){console.error('SQL ASSERTIONS',e.message,e.detail||'',e.where||'');process.exitCode=1}
 await db.exec('ROLLBACK');
 console.log('rollback counts',JSON.stringify((await db.query('SELECT (SELECT count(*) FROM schools) schools,(SELECT count(*) FROM scan_live_sessions) sessions')).rows));
 await db.close();
})().catch(e=>{console.error(e);process.exit(1)});
