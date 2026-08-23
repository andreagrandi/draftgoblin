import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: root

    required property var sessionState
    signal settingsRequested()

    color: Theme.surfaceLow
    implicitHeight: 68

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 20
        anchors.rightMargin: 20
        spacing: 16

        ColumnLayout {
            spacing: 0

            Label {
                text: "DRAFTGOBLIN"
                color: Theme.primary
                font.pixelSize: 18
                font.bold: true
                font.letterSpacing: 1.2
            }

            Label {
                text: "TACTICAL GRIMOIRE"
                color: Theme.textMuted
                font.pixelSize: 10
                font.letterSpacing: 1.6
            }
        }

        Rectangle {
            Layout.preferredWidth: 1
            Layout.preferredHeight: 34
            color: Theme.outline
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2

            Label {
                text: root.sessionState.status ? root.sessionState.status.message : "Starting Draftgoblin."
                color: Theme.text
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            Label {
                text: root.sessionState.draft
                    ? root.sessionState.draft.set_code + " · " + root.sessionState.draft.event_name
                    : "No active draft"
                color: Theme.textMuted
                font.pixelSize: 11
            }
        }

        Label {
            text: root.sessionState.active_account
                ? root.sessionState.active_account.screen_name
                : "No Arena account"
            color: Theme.textMuted
            font.pixelSize: 12
        }

        ComboBox {
            id: scenarioSelector
            Layout.preferredWidth: 126
            model: mockProvider.scenarios
            currentIndex: Math.max(0, mockProvider.scenarios.indexOf(mockProvider.scenario))
            Accessible.name: "Representative state"
            onActivated: mockProvider.selectScenario(currentText)

            background: Rectangle {
                color: Theme.surfaceHigh
                border.color: scenarioSelector.activeFocus ? Theme.focus : Theme.outline
                border.width: scenarioSelector.activeFocus ? 2 : 1
                radius: Theme.radius
            }
        }

        Button {
            text: "Settings"
            Accessible.name: "Open settings"
            onClicked: root.settingsRequested()
        }
    }

    Rectangle {
        anchors.bottom: parent.bottom
        width: parent.width
        height: 1
        color: Theme.outline
    }
}

