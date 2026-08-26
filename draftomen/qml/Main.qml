import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ApplicationWindow {
    id: window
    required property var provider

    width: initialWindowWidth
    height: initialWindowHeight
    minimumWidth: 680
    minimumHeight: 640
    visible: true
    title: applicationTitle
    color: Theme.background

    property string currentSurface: initialSurface
    readonly property bool narrow: width < Theme.narrowBreakpoint
    readonly property var sessionState: provider.state
    readonly property var displayPreferences: guiPreferences
    property string automaticBuildContext: ""
    readonly property string desktopApplicationVersion: applicationVersion

    function requestCompletedDraftBuild() {
        const errors = window.sessionState ? window.sessionState.errors : null
        if (errors && errors.some(error => error && error.operation === "build")) {
            window.automaticBuildContext = ""
            return
        }

        const progress = window.sessionState ? window.sessionState.progress : null
        if (progress && progress.operation === "build")
            return

        if (window.currentSurface !== "build")
            return

        const draft = window.sessionState ? window.sessionState.draft : null
        const pool = window.sessionState ? window.sessionState.pool : null
        const cardData = window.sessionState ? window.sessionState.card_data : null
        if (!draft || !draft.completed || !pool || pool.total_cards <= 0
                || !cardData || cardData.phase !== "ready"
                || window.sessionState.build)
            return

        const context = draft.account_id + ":" + draft.draft_id
        if (window.automaticBuildContext === context)
            return

        window.automaticBuildContext = context
        window.provider.requestBuild("")
    }

    onCurrentSurfaceChanged: Qt.callLater(window.requestCompletedDraftBuild)
    onSessionStateChanged: Qt.callLater(window.requestCompletedDraftBuild)
    Component.onCompleted: Qt.callLater(window.requestCompletedDraftBuild)

    header: AppBar {
        narrow: window.narrow
        sessionState: window.sessionState
        provider: window.provider
        onSettingsRequested: window.currentSurface = "settings"
    }

    footer: StatusStrip {
        sessionState: window.sessionState
        applicationVersion: window.desktopApplicationVersion
        narrow: window.narrow
        onAboutRequested: opener => {
            aboutDialog.returnFocusItem = opener
            aboutDialog.open()
        }
        onPrivacyRequested: opener => {
            privacyDialog.returnFocusItem = opener
            privacyDialog.open()
        }
    }

    AboutDialog {
        id: aboutDialog
        applicationVersion: window.desktopApplicationVersion
    }

    PrivacyDialog {
        id: privacyDialog
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        NavigationRail {
            Layout.fillHeight: true
            currentSurface: window.currentSurface
            compact: window.narrow
            onSurfaceRequested: surface => window.currentSurface = surface
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: window.narrow ? 12 : 20
            currentIndex: {
                if (window.currentSurface === "build") return 1
                if (window.currentSurface === "backtest") return 2
                if (window.currentSurface === "settings") return 3
                return 0
            }

            LiveDraftView {
                sessionState: window.sessionState
                recommendationModel: window.provider.recommendationsModel
                narrow: window.narrow
                displayPreferences: window.displayPreferences
            }

            BuildView {
                sessionState: window.sessionState
                narrow: window.narrow
                displayPreferences: window.displayPreferences
            }

            BacktestView {
                sessionState: window.sessionState
                narrow: window.narrow
                displayPreferences: window.displayPreferences
            }

            SettingsView {
                sessionState: window.sessionState
                narrow: window.narrow
                displayPreferences: window.displayPreferences
            }
        }
    }
}

