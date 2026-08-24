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

    header: AppBar {
        sessionState: window.sessionState
        provider: window.provider
        onSettingsRequested: window.currentSurface = "settings"
    }

    footer: StatusStrip {
        sessionState: window.sessionState
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

