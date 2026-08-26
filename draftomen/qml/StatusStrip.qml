import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: root
    objectName: "statusStrip"

    required property var sessionState
    required property string applicationVersion
    property bool narrow: false
    signal aboutRequested(var opener)
    signal privacyRequested(var opener)

    color: Theme.surfaceLow
    implicitHeight: 34

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        spacing: 12

        Rectangle {
            Layout.preferredWidth: 8
            Layout.preferredHeight: 8
            radius: 4
            color: root.sessionState.errors && root.sessionState.errors.length > 0
                ? Theme.error
                : root.sessionState.progress ? Theme.warning : Theme.primary
        }

        Label {
            Layout.fillWidth: true
            text: root.sessionState.status ? root.sessionState.status.message : "Starting"
            color: Theme.textMuted
            font.pixelSize: 11
            elide: Text.ElideRight
        }

        Label {
            text: root.sessionState.ratings ? root.sessionState.ratings.message : "Ratings unavailable"
            color: Theme.textMuted
            font.pixelSize: 11
            elide: Text.ElideRight
        }

        Button {
            id: aboutButton
            objectName: "aboutLink"
            Layout.preferredWidth: 64
            text: "About"
            Accessible.name: "Open About dialog"
            Accessible.description: "Show Draft Omen information and project website."
            onClicked: root.aboutRequested(aboutButton)
        }

        Button {
            id: privacyButton
            objectName: "privacyLink"
            Layout.preferredWidth: 64
            text: "Privacy"
            activeFocusOnTab: true
            focusPolicy: Qt.StrongFocus
            Accessible.role: Accessible.Button
            Accessible.name: "Open Privacy dialog"
            Accessible.description: "Show how Draft Omen handles your data."
            onClicked: root.privacyRequested(privacyButton)
        }

        Label {
            objectName: "applicationVersionLabel"
            text: "v" + root.applicationVersion
            color: Theme.textMuted
            font.pixelSize: 11
            Accessible.name: "Draft Omen version " + root.applicationVersion
        }

        Label {
            visible: !root.narrow
            text: "Data from 17Lands"
            color: Theme.primary
            font.pixelSize: 11
        }
    }

    Rectangle {
        anchors.top: parent.top
        width: parent.width
        height: 1
        color: Theme.outline
    }
}

