import TelegramCore
#if os(macOS)
import PasscodeCore
#endif

/// nil input requests authority without migrating legacy metadata. A nil result
/// means no metadata rewrite is needed.
///
/// This fork's `PostboxAccessChallengeData` has no secured-reference case: the
/// managed credential lives in the keychain and the legacy metadata (a plaintext
/// or numerical password) is left as it is, so the fork's own passcode keeps
/// working while the store manages the same secret.
@available(macOS 10.15, *)
private func resolveAccessChallenge(_ current: PostboxAccessChallengeData?, allowMigration: Bool) throws -> PostboxAccessChallengeData? {
    let credentials = PasscodeCredentialStore.shared
    if current != nil {
        credentials.requireExistingCredential()
    }
    guard allowMigration, let current else {
        return nil
    }
    switch current {
    case let .numericalPassword(code):
        let kind: PasscodeKind = code.count == 6 ? .digits6 : .digits4
        let _ = try credentials.migrateLegacy(code: code, kind: kind)
        return nil
    case let .plaintextPassword(code):
        let _ = try credentials.migrateLegacy(code: code, kind: .alphanumeric)
        return nil
    case .none:
        return nil
    }
}

@available(macOS 10.15, *)
public func setupAccountManager<Types: AccountManagerTypes>(basePath: String, isTemporary: Bool, isReadOnly: Bool, useCaches: Bool, removeDatabaseOnError: Bool, resetLocalSecrets: (() throws -> Void)? = nil) -> AccountManager<Types> {
    let isMainProcess = PasscodeEnvironment.shared.isMainApp
    let canReset = isMainProcess && !isTemporary && !isReadOnly
    precondition(!canReset || resetLocalSecrets != nil)

    // This fork's AccountManager has no access-challenge plumbing: the credential
    // store is prepared inline. `resetLocalSecrets` is intentionally not invoked —
    // the fork reports no fresh-installation signal, and a wrong reset would wipe
    // live wallet secrets; `resumeCleanup` prunes stale state instead.
    do {
        let _ = try PasscodeEnvironment.shared.sharedAccessGroup()
        PasscodeCredentialStore.shared.configureLock(directory: basePath)
    } catch {
    }
    if isMainProcess {
        try? PasscodeCredentialStore.shared.resumeCleanup()
    }

    return AccountManager(
        basePath: basePath,
        isTemporary: isTemporary,
        isReadOnly: isReadOnly,
        useCaches: useCaches,
        removeDatabaseOnError: removeDatabaseOnError
    )
}
