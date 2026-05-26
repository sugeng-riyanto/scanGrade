-- Profiles RLS Policies
CREATE POLICY "profiles_select_own" ON profiles
    FOR SELECT USING (auth.uid() = id);

CREATE POLICY "profiles_update_own" ON profiles
    FOR UPDATE USING (auth.uid() = id);

CREATE POLICY "profiles_insert_trigger" ON profiles
    FOR INSERT WITH CHECK (auth.uid() = id);
